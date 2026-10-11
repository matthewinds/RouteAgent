"""Partial traffic is actionable evidence, never a certified complete ETA."""
from dataclasses import replace
from datetime import timedelta
import httpx
import pytest
from test_online import make_agent, request_fields, Model
from route_agent.config import Settings
from route_agent.coordinator import WeatherArgs
from route_agent.providers import Providers, now_utc
from route_agent.ranking import rank_routes, congestion_bounds, estimated_arrival_note
from route_agent.routing import assemble
from route_agent.tools import Budget
from route_agent.traffic import apply_traffic
from route_agent.verification import verify_route
from route_agent.weather import weather_context


def test_lta_resume_fetches_next_page_and_stops_when_complete(tmp_path,monkeypatch):
    monkeypatch.setenv("LTA_API_KEY","test-lta")
    settings=Settings(root=tmp_path);provider=Providers(settings,Budget(settings));seen=[]
    def response(self,method,url,**kwargs):
        skip=kwargs["params"]["$skip"];seen.append(skip)
        rows=[{"LinkID":str(skip+i)} for i in range(500 if skip<1000 else 7)]
        return httpx.Response(200,json={"value":rows},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    for expected in (500,1000,1007):
        rows,refs=provider.lta("v4/TrafficSpeedBands",300,allow_partial=True,http_limit=1,resume=True)
        assert len(rows)==expected
    assert seen==[0,500,1000] and len(refs)==3
    provider.lta("v4/TrafficSpeedBands",300,allow_partial=True,http_limit=1,resume=True)
    assert seen==[0,500,1000] and provider.lta_status["v4/TrafficSpeedBands"]["complete"]


def test_incidents_are_not_starved_by_band_time_limit(tmp_path,monkeypatch):
    monkeypatch.setenv("LTA_API_KEY","test-lta")
    settings=Settings(root=tmp_path);provider=Providers(settings,Budget(settings));seen=[]
    def response(self,method,url,**kwargs):
        seen.append(url)
        rows=[{"LinkID":str(i)} for i in range(500)] if url.endswith("TrafficSpeedBands") else []
        if url.endswith("TrafficSpeedBands"):
            provider.request_deadline=0.000001
        return httpx.Response(200,json={"value":rows},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    payload=provider.traffic()
    assert len(payload["speed_bands"])==500
    assert seen[-1].endswith("TrafficIncidents")
    assert payload["collection"]["TrafficIncidents"]["complete"]


def test_agent_can_improve_coverage_but_default_retries_reuse_and_limit_is_bounded(tmp_path):
    agent=make_agent(tmp_path,mode="driving",max_duration_s=3600,prefer_avoid_congestion=True)
    agent.plan_candidates();calls=[]
    def traffic(continue_collection=False):
        calls.append(continue_collection)
        end=1.295 if len(calls)==1 else 1.30
        lon=103.855 if len(calls)==1 else 103.86
        return {"speed_bands":[{"StartLat":1.29,"StartLon":103.85,"EndLat":end,"EndLon":lon,
            "MinimumSpeed":20,"MaximumSpeed":40}],"incidents":[],"retrieved_at":now_utc().isoformat(),
            "evidence_ids":["osrm"],"collection":{"speed":{"complete":False}}}
    agent.providers.traffic=traffic
    agent.get_traffic();first=next(iter(agent.routes.values())).traffic_coverage
    assert agent.get_traffic()["reused"] and calls==[False]
    agent.get_traffic(continue_collection=True)
    assert next(iter(agent.routes.values())).traffic_coverage>first
    agent.get_traffic(continue_collection=True)
    assert agent.get_traffic(continue_collection=True)["continuation_limit_reached"]
    assert calls==[False,True,True]


def test_partial_estimate_combines_sources_without_passing_deadline(tmp_path):
    # The partial estimate (~357s) fits the limit, but the pessimistic bound (~387s) does not.
    agent=make_agent(tmp_path,mode="driving",max_duration_s=380)
    leg=agent.providers.routes(agent.providers.a,agent.providers.b,"driving")[0]
    payload={"retrieved_at":now_utc().isoformat(),"evidence_ids":["osrm"],"speed_bands":[{
        "StartLat":1.29,"StartLon":103.85,"EndLat":1.295,"EndLon":103.855,"MinimumSpeed":20,"MaximumSpeed":40}]}
    apply_traffic([leg],payload,agent.request.departure_time)
    assert 0<leg.traffic_coverage<1 and leg.partial_traffic_duration_s is not None
    route=assemble([leg],agent.request,agent.providers.a,agent.providers.b)
    expected=leg.provider_duration_s*(1-leg.traffic_coverage)+leg.distance_m*leg.traffic_coverage/(30/3.6)
    assert route.estimated_total_s==pytest.approx(expected)
    assert route.total_s is None
    verify_route(route,agent.request)
    assert route.feasibility=="unverified"
    assert next(c for c in route.checks if c.constraint=="duration").status=="unknown"
    assert "部分路况" in estimated_arrival_note(route,agent.request)
    route.legs[0].traffic_retrieved_at=now_utc()-timedelta(hours=1)
    verify_route(route,agent.request)
    assert route.estimated_total_s is None and route.congestion_fraction is None


def test_unobserved_roads_do_not_count_as_clear_in_comparison(tmp_path):
    agent=make_agent(tmp_path,mode="driving",prefer_avoid_congestion=True);agent.plan_candidates()
    base=next(iter(agent.routes.values()))
    little=base.model_copy(update={"id":"little","traffic_coverage":.05,"congestion_fraction":0})
    more=base.model_copy(update={"id":"more","traffic_coverage":.9,"congestion_fraction":.1})
    assert congestion_bounds(little)==pytest.approx((0,.95))
    assert congestion_bounds(more)==pytest.approx((.09,.19))
    ranked,_=rank_routes([little,more],agent.request)
    assert ranked[0].id=="more"


def test_default_now_is_refreshed_after_confirmation_but_explicit_time_is_preserved(tmp_path):
    agent=make_agent(tmp_path);agent.request=None
    old=now_utc()-timedelta(hours=1)
    agent.clarifications={"departure_time":old.isoformat(),"departure_time_source":"default_now"}
    agent.set_request(request_fields(mode=None))
    assert agent.request.departure_time>old+timedelta(minutes=59)
    assert agent.result.context["departure_time_source"]=="default_now"
    agent=make_agent(tmp_path);agent.request=None
    agent.clarifications={"departure_time":old.isoformat(),"departure_time_source":"explicit"}
    agent.set_request(request_fields(mode=None))
    assert agent.request.departure_time==old and agent.result.context["departure_time_source"]=="explicit"


def test_weather_accepts_all_known_candidate_ids_instead_of_failing_at_sixteen():
    assert len(WeatherArgs(route_ids=[str(i) for i in range(19)]).route_ids)==19


def test_weather_window_tracks_labelled_partial_time_when_available(tmp_path):
    agent=make_agent(tmp_path,mode="driving");agent.plan_candidates()
    route=next(iter(agent.routes.values()))
    route.estimated_total_s=3600
    context=weather_context(route,agent.request,agent.providers.weather(),agent.providers.evidence)
    assert (context.valid_end-context.valid_start).total_seconds()==3600
    assert "部分路况" in context.timing_basis and "未知" in context.timing_basis


def test_internal_time_source_is_not_sent_as_a_user_request_field(tmp_path):
    import json
    agent=make_agent(tmp_path);agent.request=None
    agent.clarifications={"departure_time_source":"default_now","departure_time":(now_utc()-timedelta(hours=1)).isoformat()}
    model=Model([("set_request",{"fields":{"mode":None}})])
    agent.run(model)
    visible=json.loads(model.seen[0]["messages"][1]["content"])["user_clarifications"]
    assert "departure_time_source" not in visible
    assert agent.result.context["departure_time_source"]=="default_now"


def write_index(settings,pages):
    import json,time
    from route_agent.lta_index import index_path
    path=index_path(settings);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({"created":time.time(),"page_size":500,"pages":[{"skip":skip,"links":links} for skip,links in pages.items()]}))


def band(ident,slat,slon,elat,elon):
    return {"LinkID":ident,"StartLat":str(slat),"StartLon":str(slon),"EndLat":str(elat),"EndLon":str(elon),
        "MinimumSpeed":"20","MaximumSpeed":"39","RoadName":"X"}


def test_targeted_traffic_reads_only_pages_along_candidate_geometry(tmp_path,monkeypatch):
    monkeypatch.setenv("LTA_API_KEY","test-lta")
    settings=Settings(root=tmp_path)
    rows={0:[band("far",1.40,103.70,1.401,103.701)],500:[band("a",1.290,103.850,1.295,103.855)],
          1000:[band("b",1.295,103.855,1.300,103.860)]}
    write_index(settings,{skip:[[r["LinkID"],float(r["StartLat"]),float(r["StartLon"]),float(r["EndLat"]),float(r["EndLon"])] for r in page]
        for skip,page in rows.items()})
    rows[1000]=[band("b-renumbered",1.295,103.855,1.300,103.860)]  # live page drifted from the index
    provider=Providers(settings,Budget(settings));seen=[]
    def response(self,method,url,**kwargs):
        skip=kwargs["params"]["$skip"]
        if url.endswith("TrafficIncidents"):
            return httpx.Response(200,json={"value":[]},request=httpx.Request(method,url))
        seen.append(skip)
        return httpx.Response(200,json={"value":rows[skip]},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    payload=provider.traffic_targeted([[[103.850,1.290],[103.855,1.295],[103.860,1.300]]])
    assert sorted(seen)==[500,1000]
    status=payload["collection"]["v4/TrafficSpeedBands"]
    assert status["mode"]=="route_targeted" and status["needed_pages"]==2 and status["complete"]
    assert status["index_drift_pages"]==1 and "索引" in status["reason"]
    assert {b["LinkID"] for b in payload["speed_bands"]}=={"a","b-renumbered"}
    provider.traffic_targeted([[[103.850,1.290],[103.855,1.295],[103.860,1.300]]])
    assert sorted(seen)==[500,1000]  # Fresh pages are reused, not queried again.


def test_targeted_traffic_needs_an_index(tmp_path,monkeypatch):
    monkeypatch.setenv("LTA_API_KEY","test-lta")
    settings=Settings(root=tmp_path)
    assert Providers(settings,Budget(settings)).traffic_targeted([[[103.85,1.29],[103.86,1.30]]]) is None


def test_agent_fetches_traffic_again_only_for_new_driving_geometry(tmp_path):
    agent=make_agent(tmp_path,mode="driving",max_duration_s=3600)
    calls=[]
    def targeted(geometries,continue_collection=False):
        calls.append(len(geometries))
        return {"speed_bands":[],"incidents":[],"retrieved_at":now_utc().isoformat(),"evidence_ids":["osrm"],
            "collection":{"v4/TrafficSpeedBands":{"complete":True,"mode":"route_targeted"}}}
    agent.providers.traffic_targeted=targeted
    agent.plan_candidates()
    agent.get_traffic()
    assert calls==[1]
    assert agent.get_traffic()["reused"] and calls==[1]
    agent.legs[("x",)]=[agent.providers.routes(agent.providers.b,agent.providers.a,"driving")[0]]
    agent.get_traffic()
    assert calls==[1,2]


def half_covered_traffic():
    return {"speed_bands":[{"StartLat":1.29,"StartLon":103.85,"EndLat":1.295,"EndLon":103.855,
        "MinimumSpeed":20,"MaximumSpeed":40}],"incidents":[],"retrieved_at":now_utc().isoformat(),
        "evidence_ids":["osrm"],"collection":{}}


@pytest.mark.parametrize("limit,expected",[(3600,"pass"),(380,"unknown")])
def test_partial_coverage_passes_only_under_a_pessimistic_bound(tmp_path,limit,expected):
    agent=make_agent(tmp_path,mode="driving",max_duration_s=limit)
    agent.plan_candidates()
    agent.providers.traffic=lambda continue_collection=False:half_covered_traffic()
    agent.get_traffic()
    route=next(iter(agent.routes.values()))
    assert .5<=route.traffic_coverage<1 and route.total_s is None
    verify_route(route,agent.request,agent.providers.evidence)
    check=next(c for c in route.checks if c.constraint=="duration")
    assert check.status==expected
    if expected=="pass":
        assert "保守" in check.reason and "不是实时 ETA" in check.reason


def test_zero_minimum_band_uses_labelled_crawl_floor_in_bound(tmp_path):
    agent=make_agent(tmp_path,mode="driving",max_duration_s=3600)
    agent.plan_candidates()
    payload=half_covered_traffic()
    payload["speed_bands"][0].update(MinimumSpeed=0,MaximumSpeed=9)
    agent.providers.traffic=lambda continue_collection=False:payload
    agent.get_traffic()
    route=next(iter(agent.routes.values()));leg=route.legs[0]
    assert leg.traffic_upper_s is None and leg.bounded_upper_s is not None
    verify_route(route,agent.request,agent.providers.evidence)
    check=next(c for c in route.checks if c.constraint=="duration")
    assert check.status=="pass" and "3 km/h" in check.reason
