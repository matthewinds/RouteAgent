"""Regressions from the driving/refuelling result; no live API calls."""
import json
from datetime import timedelta
import pytest
from test_online import make_agent
from route_agent.config import Settings
from route_agent.models import Place
from route_agent.providers import Providers, now_utc
from route_agent.ranking import diverse_references, historical_traffic, place_label, poi_search_summary, time_notes
from route_agent.tools import Budget, ToolFailure
from route_agent.verification import verify_route


def test_old_departure_does_not_download_irrelevant_live_traffic(tmp_path):
    departure=now_utc()-timedelta(minutes=19)
    agent=make_agent(tmp_path,mode="driving",departure_time=departure,
        arrival_deadline=departure+timedelta(hours=1),prefer_avoid_congestion=True)
    agent.providers.traffic=lambda:pytest.fail("Live traffic cannot reconstruct the past")
    agent.plan_candidates()
    for _ in range(2):
        assert agent.get_traffic()["applicability"]=="past_departure"
    route=next(iter(agent.routes.values()))
    verify_route(route,agent.request)
    assert route.total_s is None and route.feasibility=="unverified"
    assert agent.traffic_attempted and agent.traffic is None
    context=agent.result.context["traffic"]
    assert historical_traffic(agent.request,context)
    notes=time_notes(route,context,agent.request)
    assert len(notes)==1 and "还原" in notes[0] and "0%" not in notes[0]


def test_reselecting_current_mode_is_harmless_but_changes_stay_sealed(tmp_path):
    agent=make_agent(tmp_path,mode="driving",car_access=True)
    assert agent.choose_mode("driving")["already_selected"]
    with pytest.raises(ToolFailure) as error:
        agent.choose_mode("walking")
    assert error.value.code=="immutable_request"


def test_available_forecast_does_not_prove_rain_shelter(tmp_path):
    agent=make_agent(tmp_path,mode="driving",prefer_avoid_rain=True)
    agent.plan_candidates();agent.get_weather();agent.validate_routes([])
    route=next(iter(agent.routes.values()))
    checks={c.constraint:c for c in route.checks}
    assert checks["weather_reference"].status=="pass"
    assert checks["rain_exposure"].status=="unknown" and not checks["rain_exposure"].hard
    agent.finish_plan(route.id,reason_codes=["weather"])
    assert agent.result.status=="unverified" and "遮蔽" in agent.result.context["unmet_preferences"][0]


def test_references_keep_best_path_per_stop_before_repeated_paths(tmp_path):
    agent=make_agent(tmp_path,mode="driving");agent.plan_candidates()
    base=next(iter(agent.routes.values()))
    first=Place(id="a",name="SPC",lat=1.29,lon=103.84,source="OSM Overpass")
    second=first.model_copy(update={"id":"b","lon":103.85})
    third=first.model_copy(update={"id":"c","lon":103.86})
    ranked=[base.model_copy(update={"id":str(i),"poi":p}) for i,p in enumerate([first,first,first,second,third])]
    assert [r.id for r in diverse_references(ranked)]==["0","3","4"]
    assert [r.id for r in diverse_references(ranked[:2])]==["0","1"]
    assert place_label(first)!=place_label(second)


def test_osm_stop_preserves_sourced_address_and_map_link(tmp_path,monkeypatch):
    settings=Settings(root=tmp_path);provider=Providers(settings,Budget(settings))
    center=Place(id="c",name="center",lat=1.29,lon=103.85,source="MOCK")
    monkeypatch.setattr(provider,"overpass",lambda *a:({"elements":[{"type":"way","id":123,
        "center":{"lat":1.29,"lon":103.85},"tags":{"amenity":"fuel","name":"SPC",
        "addr:housenumber":"10","addr:street":"Test Road"}}]},"ev"))
    p=provider.pois(center,"amenity:fuel")[0]
    assert p.address=="10 Test Road" and p.source_url=="https://www.openstreetmap.org/way/123"
    assert place_label(p)=="SPC · 10 Test Road"


def test_poi_search_counts_unique_stops_without_last_empty_search_erasing_them():
    searches=[{"count":0,"place_ids":[]},{"count":2,"place_ids":["a","b"]},
        {"count":2,"place_ids":["b","c"]},{"count":0,"place_ids":[]}]
    assert "4 次" in poi_search_summary(searches) and "3 个" in poi_search_summary(searches)


def weather_routes(agent):
    agent.plan_candidates();base=next(iter(agent.routes.values()))
    agent.routes={}
    for i in range(4):
        route=base.model_copy(deep=True)
        route.id="route-"+str(i)
        route.legs[0].geometry["coordinates"]=[[103.70+i*.016,lat] for lat in (1.25,1.30,1.35)]
        agent.routes[route.id]=route


def test_weather_batches_cover_later_candidates_and_reuse_previous_samples(tmp_path):
    agent=make_agent(tmp_path,weather_required=True);weather_routes(agent)
    original=agent.providers.weather;calls=[]
    def weather(points,departure):
        calls.append(points)
        assert 1<=len(points)<=8
        return original(points,departure)
    agent.providers.weather=weather
    result=agent.get_weather(route_ids=list(agent.routes))
    assert len(calls)==2 and result["sample_count"]>8
    assert not result["unknown_route_ids"]
    assert all(r.weather.status=="available" for r in agent.routes.values())
    agent.get_weather(route_ids=list(agent.routes))
    assert len(calls)==2


def test_weather_partial_batch_failure_keeps_success_and_can_replan_query(tmp_path):
    agent=make_agent(tmp_path,weather_required=True);weather_routes(agent)
    original=agent.providers.weather;calls=[]
    def weather(points,departure):
        calls.append(points)
        if len(calls)==2:
            raise ToolFailure("MOCK unavailable","network_error")
        return original(points,departure)
    agent.providers.weather=weather
    result=agent.get_weather()
    assert result["failures"] and result["unknown_route_ids"] and result["sample_count"]==8
    assert agent.routes["route-0"].weather.status=="available"
    first_samples=[p["requested_coordinate"] for p in agent.weather["points"]]
    result=agent.get_weather(route_ids=result["unknown_route_ids"])
    assert not result["unknown_route_ids"]
    assert all(p in [x["requested_coordinate"] for x in agent.weather["points"]] for p in first_samples)


def test_traffic_refresh_keeps_route_families_when_mode_is_unspecified(tmp_path):
    agent=make_agent(tmp_path,mode=None,car_access=True)
    agent.plan_candidates(modes=["walking","driving"])
    for route in agent.routes.values():
        route.source_warnings.append("kept")
    agent.providers.traffic=lambda continue_collection=False:{"speed_bands":[],"incidents":[],
        "retrieved_at":now_utc().isoformat(),"evidence_ids":[],"collection":{}}
    output=agent.execute("get_traffic","{}","traffic-1")
    assert "error" not in output
    modes={route.mode:route for route in agent.routes.values()}
    assert set(modes)=={"walking","driving"}
    assert modes["walking"].fare_sgd==0
    assert all("kept" in route.source_warnings for route in agent.routes.values())


def test_tool_call_reserve_leaves_room_to_publish(tmp_path):
    from dataclasses import replace
    agent=make_agent(tmp_path,mode="walking",poi_category="shop:bakery")
    agent.settings=agent.budget.settings=replace(agent.settings,max_tool_calls=6,completion_reserve_tool_calls=4)
    agent.plan_candidates()
    agent.execute("validate_routes","{}","v-1")
    agent.execute("compare_routes","{}","c-1")
    blocked=agent.execute("search_pois","{}","s-1")
    assert blocked["error"]=="search_budget"
    route_id=next(iter(agent.routes))
    finished=agent.execute("finish_plan",json.dumps({"selected_id":route_id}),"f-1")
    assert "error" not in finished and agent.result.recommended.id==route_id


def test_low_cost_note_checks_actual_candidate_fares(tmp_path):
    agent=make_agent(tmp_path,mode=None,car_access=True,prefer_low_cost=True)
    agent.candidate_modes=lambda:["walking","driving"]
    agent.plan_candidates()
    walking=next(r for r in agent.routes.values() if r.mode=="walking")
    agent.finish_plan(selected_id=walking.id)
    assert any("费用未知" in note for note in agent.result.context["unmet_preferences"])
    assert agent.result.status=="unverified"


def test_resolve_place_replay_restores_resolved_role(tmp_path):
    agent=make_agent(tmp_path,mode="walking")
    agent.roles.clear()
    first=agent.execute("resolve_place",json.dumps({"role":"origin"}),"r-1")
    assert first["resolved"]["id"]=="origin" and "origin" in agent.roles
    agent.execute("resolve_place",json.dumps({"role":"origin","search_queries":["Elsewhere"]}),"r-2")
    assert "origin" not in agent.roles
    again=agent.execute("resolve_place",json.dumps({"role":"origin"}),"r-3")
    assert again["resolved"]["id"]=="origin" and "origin" in agent.roles


def test_confirmed_place_id_as_candidate_replays_confirmed_search(tmp_path):
    agent=make_agent(tmp_path,mode="walking")
    agent.roles.clear()
    agent.clarifications={"origin_place_id":"origin",
        "place_searches":{"origin":{"query":"City Hall","search_queries":["Elsewhere"],"source":"map"}}}
    output=agent.execute("resolve_place",json.dumps({"role":"origin","candidate_ids":["origin"]}),"r-1")
    assert output["resolved"]["id"]=="origin" and output["user_confirmed"] and "origin" in agent.roles
    other=agent.execute("resolve_place",json.dumps({"role":"destination","candidate_ids":["invented"]}),"r-2")
    assert other["error"]=="unknown_id"


def test_both_endpoint_choices_are_asked_in_one_round(tmp_path):
    agent=make_agent(tmp_path,mode="walking")
    agent.roles.clear()
    agent.providers.geocode=lambda query:[agent.providers.a if "Hall" in query else agent.providers.b]
    first=agent.resolve_place("origin",["City Hall MRT"])
    assert first["awaiting_user_choice"]==["origin_place_id"] and not agent.done
    agent.resolve_place("destination",["Marina Bay Sands"])
    assert agent.done and set(agent.result.questions)=={"origin_place_id","destination_place_id"}
    assert {"origin_place_id","destination_place_id"}<=set(agent.result.context["choices"])


def test_violated_selection_is_still_rejected(tmp_path):
    agent=make_agent(tmp_path,mode="walking",max_walking_m=10)
    agent.plan_candidates()
    ident=next(iter(agent.routes))
    with pytest.raises(ToolFailure,match="不能发布"):
        agent.finish_plan(ident)


def test_web_confirmed_place_replays_in_a_new_session(tmp_path):
    """A web-sourced confirmation must survive a new run whose document IDs differ."""
    from route_agent.web_locations import store_document, source_candidate, recover_document
    page="Hotel 1900 @ Chinatown is located at 48 Mosque Street, Singapore 059526."
    def agent_with_fresh_session(revision):
        agent=make_agent(tmp_path,mode="walking")
        agent.request.destination="牛车水的hotel 1900"
        agent.roles.pop("destination")
        p=agent.providers
        p.location_documents={}
        mosque=p.b.model_copy(update={"id":"pelias:mosque","name":"48 Mosque Street"})
        p.geocode=lambda query:[mosque]
        # Search excerpts differ between sessions, so document IDs differ too.
        p.web_location_sources=lambda query:{"documents":[store_document(p,"https://hotel.example/about",
            page+" Revision "+revision,["geo"])],"failures":[],"search_configured":True,"next_step":"read"}
        p.web_location_candidate=lambda *args:source_candidate(p,*args)
        p.recover_location_document=lambda url,quote:recover_document(p,url,quote)
        return agent
    first=agent_with_fresh_session("A")
    docs=first.resolve_place("destination",["Hotel 1900"],source="web")["documents"]
    quote="Hotel 1900 @ Chinatown is located at 48 Mosque Street"
    shortlist=first.resolve_place("destination",["Hotel 1900"],source="web",web_document_id=docs[0]["id"],
        place_name="48 Mosque Street",company_name="Hotel 1900",source_quote=quote,present_candidates=True)
    chosen=first.choices["destination_place_id"][0]["id"]
    saved=first.result.context["place_searches"]
    assert saved["destination"]["source_url"]=="https://hotel.example/about"
    second=agent_with_fresh_session("B")
    second.clarifications={"destination_place_id":chosen,"place_searches":saved}
    output=second.resolve_place("destination")
    assert output["resolved"]["id"]==chosen and output["user_confirmed"] and "destination" in second.roles


def test_round_limit_publishes_real_candidates_as_labelled_references(tmp_path):
    from dataclasses import replace
    from test_online import Model
    agent=make_agent(tmp_path,mode="walking")
    agent.settings=agent.budget.settings=replace(agent.settings,max_rounds=2)
    agent.run(Model([("plan_candidates",{}),("compare_routes",{})]))
    result=agent.result
    assert result.status=="unverified" and result.recommended is None
    assert result.unverified and result.unverified[0].id in agent.routes
    assert "轮数已用完" in result.message and result.context["auto_completed"]["reason"]=="model_rounds"


def test_round_limit_without_candidates_still_reports_failure(tmp_path):
    from dataclasses import replace
    from test_online import Model
    agent=make_agent(tmp_path,mode="walking")
    agent.settings=agent.budget.settings=replace(agent.settings,max_rounds=1)
    with pytest.raises(ToolFailure,match="轮数上限"):
        agent.run(Model([("compare_routes",{})]))


def test_chinese_numeral_deadline_is_kept_and_cannot_be_dropped():
    from route_agent.parser import validate_request,SG
    from datetime import datetime,timedelta
    text='我现在要从NUS前往牛车水的hotel 1900，我需要在十二点前到达'
    base={'origin':'NUS','destination':'牛车水的hotel 1900'}
    with pytest.raises(ToolFailure,match='arrival_deadline'):
        validate_request(text,base,{})
    deadline=(datetime.now(SG)+timedelta(hours=1)).isoformat()
    req,_=validate_request(text,{**base,'arrival_deadline':deadline,'evidence':{'arrival_deadline':'我需要在十二点前到达'}},{})
    assert req.arrival_deadline is not None


def test_ambiguous_twelve_hour_deadline_far_ahead_is_confirmed_with_user():
    from route_agent.parser import validate_request,SG
    from datetime import datetime
    text='我需要在十二点前到达'
    departure=datetime(2026,10,10,23,14,tzinfo=SG)
    fields={'origin':'NUS','destination':'Hotel 1900','departure_time':departure.isoformat(),
        'arrival_deadline':datetime(2026,10,11,12,0,tzinfo=SG).isoformat(),'evidence':{'arrival_deadline':text,'departure_time':'现在'}}
    _,questions=validate_request('现在出发，'+text,fields,{})
    assert 'arrival_deadline' in questions and '确认' in questions['arrival_deadline']
    fields['evidence']['arrival_deadline']='我需要在明天中午十二点前到达'
    _,questions=validate_request('现在出发，我需要在明天中午十二点前到达',fields,{})
    assert 'arrival_deadline' not in questions


def test_model_timeout_after_candidates_publishes_labelled_references(tmp_path):
    from test_online import Model
    import httpx
    from openai import APITimeoutError
    agent=make_agent(tmp_path,mode="walking")
    model=Model([("plan_candidates",{})])
    original=model.create
    def create(**kwargs):
        if len(model.seen)>=1:
            raise APITimeoutError(request=httpx.Request("POST","https://api.deepseek.test"))
        return original(**kwargs)
    model.chat.completions.create=create
    result=agent.run(model)
    assert result.status=="unverified" and result.recommended is None and result.unverified
    assert result.context["auto_completed"]["reason"]=="model_timeout" and "超时" in result.message


def test_references_keep_each_travel_family_visible():
    from types import SimpleNamespace as NS
    def route(ident,mode,poi):
        return NS(id=ident,mode=mode,poi=NS(id=poi),parking=None)
    ranked=[route("t1","transit","s1"),route("t2","transit","s2"),route("t3","transit","s3"),
            route("w1","walking","s1"),route("d1","driving","s1")]
    assert [r.id for r in diverse_references(ranked)]==["t1","w1","d1"]


def test_ambiguous_deadline_question_carries_both_clock_readings(tmp_path):
    from datetime import datetime
    from route_agent.parser import SG
    agent=make_agent(tmp_path,mode="walking");agent.request=None
    agent.result.request.original_text="我现在从NUS出发，我需要在十二点前到达"
    agent.clarifications={"departure_time":datetime(2026,10,10,23,38,tzinfo=SG).isoformat(),"departure_time_source":"explicit"}
    agent.set_request({"origin":"NUS","destination":"Hotel 1900","arrival_deadline":datetime(2026,10,11,12,0,tzinfo=SG).isoformat(),
        "evidence":{"arrival_deadline":"我需要在十二点前到达","origin":"我现在从NUS出发"}})
    assert "arrival_deadline" in agent.result.questions
    names=[x["name"] for x in agent.result.context["choices"]["arrival_deadline"]]
    assert names[0].startswith("今天午夜") and names[1].startswith("明天 12:00")


def test_unqueried_traffic_is_not_reported_as_zero_coverage(tmp_path):
    agent=make_agent(tmp_path,mode="driving")
    agent.plan_candidates()
    route=next(iter(agent.routes.values()))
    assert route.traffic_coverage is None  # Never queried, not "0% observed".


def test_auto_complete_collects_missing_traffic_for_driving(tmp_path):
    agent=make_agent(tmp_path,mode="driving",max_duration_s=3600)
    agent.plan_candidates()
    agent.providers.traffic=lambda continue_collection=False:{"speed_bands":[{"StartLat":1.29,"StartLon":103.85,
        "EndLat":1.30,"EndLon":103.86,"MinimumSpeed":20,"MaximumSpeed":40}],"incidents":[],
        "retrieved_at":now_utc().isoformat(),"evidence_ids":["osrm"],"collection":{}}
    agent.auto_complete("model_network_error")
    assert agent.traffic_attempted and "get_traffic" in agent.result.context["auto_completed_steps"]
    assert agent.result.unverified[0].traffic_coverage is not None


def multi_stop_agent(tmp_path,mode="walking",**kw):
    from route_agent.models import Place
    stops=[{"poi_category":"amenity:post_office","evidence":"去邮局寄信","stop_duration_s":300,"duration_evidence":"停留5分钟"},
           {"poi_category":"shop:bakery","evidence":"再去面包店","stop_duration_s":120,"duration_evidence":"停留2分钟"}]
    agent=make_agent(tmp_path,mode=mode,stops=stops,poi_required=True,**kw)
    places=[Place(id="post-a",name="Post A",lat=1.292,lon=103.852,category="amenity:post_office",source="OSM Overpass",evidence_ids=["geo"]),
            Place(id="post-b",name="Post B",lat=1.293,lon=103.853,category="amenity:post_office",source="OSM Overpass",evidence_ids=["geo"]),
            Place(id="bakery-a",name="Bakery A",lat=1.296,lon=103.856,category="shop:bakery",source="OSM Overpass",evidence_ids=["geo"])]
    agent.places.update({p.id:p for p in places})
    return agent


@pytest.mark.parametrize("mode",["walking","driving"])
def test_two_ordered_stops_chain_real_legs_and_count_every_dwell(tmp_path,mode):
    agent=multi_stop_agent(tmp_path,mode=mode)
    output=agent.plan_candidates(stop_poi_ids=[["post-a","post-b"],["bakery-a"]])
    routes=[agent.routes[i] for i in output["route_ids"]]
    assert len(routes)==2
    route=routes[0]
    assert [p.id for p in route.stops][1]=="bakery-a" and route.stops[0].category=="amenity:post_office"
    assert len(route.legs)==3 and route.stop_s==420 and route.stop_leg_indices==[0,1]
    assert route.provider_total_s==sum(l.provider_duration_s for l in route.legs)+420
    verify_route(route,agent.request,agent.providers.evidence)
    assert next(c for c in route.checks if c.constraint=="required_stops").status=="pass"
    compared=agent.compare_routes()["candidates"]
    assert [s["id"] for s in compared[0]["stops"]][1]=="bakery-a"
    agent.finish_plan(compared[0]["id"],reason_codes=["poi"])
    assert "依次经停" in agent.result.explanation and "Bakery A" in agent.result.explanation


def test_multi_stop_planning_rejects_missing_or_wrong_candidates(tmp_path):
    agent=multi_stop_agent(tmp_path)
    with pytest.raises(ToolFailure,match="stop_index=1"):
        agent.plan_candidates(stop_poi_ids=[["post-a"]])
    with pytest.raises(ToolFailure,match="类别"):
        agent.plan_candidates(stop_poi_ids=[["bakery-a"],["post-a"]])
    with pytest.raises(ToolFailure,match="stop_index"):
        agent.search_pois()


def test_search_pois_uses_the_requested_stops_category(tmp_path):
    agent=multi_stop_agent(tmp_path)
    seen=[]
    def along(reference,category,*args):
        seen.append(category);return []
    agent.providers.pois_along_route=along
    agent.search_pois(stop_index=1)
    assert seen and set(seen)=={"shop:bakery"}
    assert agent.result.context["poi_searches"][-1]["stop_index"]==1


def test_named_stop_in_another_language_matches_its_searched_spelling(tmp_path):
    from route_agent.models import Place
    stops=[{"poi_name":"星巴克","evidence":"先去星巴克","stop_duration_s":300,"duration_evidence":"停留5分钟"},
           {"poi_category":"shop:bakery","evidence":"再去面包店","stop_duration_s":120,"duration_evidence":"停留2分钟"}]
    agent=make_agent(tmp_path,mode="walking",stops=stops,poi_required=True)
    starbucks=Place(id="sb",name="Starbucks",lat=1.292,lon=103.852,category="cafe",source="OSM Overpass",evidence_ids=["geo"])
    other=Place(id="cb",name="Coffee Bean",lat=1.293,lon=103.853,category="cafe",source="OSM Overpass",evidence_ids=["geo"])
    bakery=Place(id="bk",name="Bakery",lat=1.296,lon=103.856,category="shop:bakery",source="OSM Overpass",evidence_ids=["geo"])
    agent.providers.pois_along_route=lambda ref,cat,r,l,name:[starbucks] if name else [bakery]
    agent.search_pois(stop_index=0,search_names=["Starbucks"])
    agent.search_pois(stop_index=1)
    agent.places["cb"]=other
    with pytest.raises(ToolFailure,match="名称"):
        agent.plan_candidates(stop_poi_ids=[["cb"],["bk"]])  # A different brand is still rejected.
    route_id=agent.plan_candidates(stop_poi_ids=[["sb"],["bk"]])["route_ids"][0]
    agent.validate_routes()
    check=next(c for c in agent.routes[route_id].checks if c.constraint=="required_stops")
    assert check.status=="pass" and check.required==["星巴克","shop:bakery"]
