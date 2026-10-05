"""Regression checks for incomplete preferences and honest transport comparisons."""
import json
from dataclasses import replace
import httpx
import pytest
from test_online import make_agent, FakeProviders, request_fields
from route_agent.config import Settings
from route_agent.models import Place, PlanningResult, TaskState, TravelRequest
from route_agent.parser import validate_request
from route_agent.providers import Providers
from route_agent.tools import Budget, ToolFailure
from route_agent.weather import weather_context


@pytest.mark.parametrize("field,text",[("prefer_low_cost","最经济实惠"),("prefer_fastest","最快，因为赶时间"),("weather_required","考虑天气因素")])
def test_explicit_preferences_cannot_be_discarded(field,text):
    with pytest.raises(ToolFailure,match="遗漏"):
        validate_request(text,request_fields(),{})
    req,_=validate_request(text,request_fields(**{field:True}),{})
    assert getattr(req,field)


def test_hurry_cannot_imply_car_ownership():
    with pytest.raises(ToolFailure,match="车辆"):
        validate_request("我要最快到达",request_fields(prefer_fastest=True,car_access=True,evidence={"car_access":"我要最快到达"}),{})
    with pytest.raises(ToolFailure,match="车辆"):
        validate_request("我没有车",request_fields(car_access=True,evidence={"car_access":"我没有车"}),{})


def test_no_available_modes_is_a_service_limitation_not_a_fake_plan(tmp_path):
    agent=make_agent(tmp_path,mode=None,car_access=False)
    def unavailable(*args):raise ToolFailure("未取得路线","no_route")
    agent.providers.routes=unavailable
    agent.compare_modes()
    assert agent.done and agent.result.status=="tool_error" and not agent.result.recommended


def test_only_car_route_is_not_labelled_as_full_mode_comparison(tmp_path):
    agent=make_agent(tmp_path,mode=None,car_access=True)
    original=agent.providers.routes
    def routes(a,b,mode,*args):
        if mode=="walking":
            raise ToolFailure("未取得步行路线","no_route")
        return original(a,b,mode,*args)
    agent.providers.routes=routes
    agent.compare_modes()
    agent.choose_mode("driving",["osrm"],["time"])
    assert "一种" in agent.result.context["mode_selection"]["basis"]
    assert {f["mode"] for f in agent.result.context["mode_comparison"]["failures"]}=={"walking","transit"}


def test_car_excluded_when_user_has_no_vehicle(tmp_path):
    agent=make_agent(tmp_path,mode=None,car_access=False)
    agent.compare_modes()
    assert "driving" not in agent.mode_options


def test_incomplete_cost_and_speed_publish_only_reference(tmp_path):
    agent=make_agent(tmp_path,mode="driving",prefer_low_cost=True,prefer_fastest=True)
    agent.traffic_attempted=True  # Complete traffic unavailable in this protocol fixture.
    agent.plan_candidates()
    ident=next(iter(agent.routes))
    agent.finish_plan(ident)  # Evidence chosen by backend, not model guesses.
    assert agent.result.status=="unverified" and agent.result.recommended
    assert {c.constraint for c in agent.result.recommended.checks if c.status=="unknown"}=={"cost","speed_reference"}
    assert agent.result.context["unmet_preferences"]


def test_missing_requested_weather_is_visible_in_result_status(tmp_path):
    agent=make_agent(tmp_path,weather_required=True)
    agent.plan_candidates(); agent.weather_attempted=True
    agent.finish_plan(next(iter(agent.routes)))
    assert agent.result.status=="unverified"
    assert any(c.constraint=="weather_reference" and c.status=="unknown" for c in agent.result.recommended.checks)


def test_weather_uses_labelled_base_window_when_traffic_time_unknown(tmp_path):
    agent=make_agent(tmp_path,mode="driving",weather_required=True)
    agent.plan_candidates(); route=next(iter(agent.routes.values()))
    assert route.total_s is None
    context=weather_context(route,agent.request,agent.providers.weather(),agent.providers.evidence)
    assert context.status=="available" and context.samples
    assert "基础" in context.timing_basis and "未知" in context.timing_basis
    assert route.total_s is None


def test_generic_checkpoint_filters_blocks_and_duplicate_street(tmp_path,monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "test-geocode-key")
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    names=[("venue:main","Tuas Checkpoint"),("street:main","Tuas Checkpoint"),("street:a3","Tuas Checkpoint - Block A3")]
    def response(*args,**kwargs):
        return {"features":[{"geometry":{"coordinates":[103.63,1.35]},"properties":{
            "gid":"openstreetmap:"+gid,"name":name,"label":name+", Singapore","confidence":1}} for gid,name in names]},"ev"
    monkeypatch.setattr(provider,"request",response)
    places=provider.geocode("Tuas Checkpoint")
    assert len(places)==1 and places[0].location_kind=="checkpoint" and places[0].requires_confirmation
    assert places[0].access_note
    assert any("Block A3" in p.name for p in provider.geocode("Tuas Checkpoint Block A3"))


def test_checkpoint_requires_real_departure_position_confirmation(tmp_path):
    agent=make_agent(tmp_path)
    p=agent.providers.a.model_copy(update={"location_kind":"checkpoint"})
    agent.providers.geocode=lambda _: [p]
    agent.clarifications={"origin_place_id":p.id}
    agent.resolve_place("origin")
    assert agent.result.questions.keys()=={"origin_access_confirmed"} and "origin" not in agent.roles
    agent.done=False; agent.clarifications["origin_access_confirmed"]=True
    agent.resolve_place("origin")
    assert agent.roles["origin"].id==p.id


def test_traffic_has_independent_request_limit_and_reuses_result(tmp_path,monkeypatch):
    monkeypatch.setenv("LTA_API_KEY","mock-lta")
    settings=replace(Settings(root=tmp_path),traffic_max_http_calls=2)
    provider=Providers(settings,Budget(settings))
    def response(self,method,url,**kwargs):
        skip=kwargs["params"]["$skip"]
        rows=[{"LinkID":str(skip+i)} for i in range(500)] if url.endswith("TrafficSpeedBands") else []
        return httpx.Response(200,json={"value":rows},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    payload=provider.traffic()
    assert len(payload["speed_bands"])==1000 and provider.budget.http_calls==3
    assert not payload["collection"]["v4/TrafficSpeedBands"]["complete"]
    assert provider.request_deadline is None
    agent=make_agent(tmp_path)
    # A completed attempt is never fetched again just to satisfy model retries.
    agent.traffic=payload
    agent.providers.traffic=lambda:pytest.fail("Repeated traffic query")
    assert agent.get_traffic()["reused"]


def test_fake_provided_evidence_is_still_rejected(tmp_path):
    agent=make_agent(tmp_path);agent.plan_candidates()
    with pytest.raises(ToolFailure,match="真实存在"):
        agent.finish_plan(next(iter(agent.routes)),evidence_ids=["invented"])


def test_mode_selection_can_use_backend_evidence_without_invented_ids(tmp_path):
    agent=make_agent(tmp_path,mode=None)
    agent.compare_modes()
    agent.choose_mode("walking",reason_codes=["time"])
    assert agent.request.mode is None and agent.result.context["mode_selection"]["selected"]=="walking"
    assert agent.result.context["mode_selection"]["evidence_ids"]==["osrm"]


def test_scheduled_trip_does_not_fetch_irrelevant_live_traffic(tmp_path):
    from datetime import timedelta
    from route_agent.providers import now_utc
    departure=now_utc()+timedelta(days=1)
    agent=make_agent(tmp_path,mode="driving",departure_time=departure,
        arrival_deadline=departure+timedelta(hours=1),prefer_avoid_congestion=True)
    agent.providers.traffic=lambda:pytest.fail("Current traffic cannot predict tomorrow")
    agent.plan_candidates()
    assert agent.get_traffic()["applicability"]=="future_departure"
    assert agent.traffic_attempted and not agent.traffic
    agent.get_traffic()  # Repeated calls also make no external requests.
    agent.validate_routes([])
    route=next(iter(agent.routes.values()))
    unknown={c.constraint:c for c in route.checks if c.status=="unknown"}
    assert set(unknown)=={"arrival_deadline","congestion_reference"}
    assert unknown["arrival_deadline"].hard and not unknown["congestion_reference"].hard
    assert "预约" in unknown["arrival_deadline"].reason
    assert route.feasibility=="unverified" and route.total_s is None


def test_scheduled_result_explains_time_slack_without_claiming_verification(tmp_path):
    from datetime import timedelta
    from route_agent.providers import now_utc
    from route_agent.ranking import base_arrival_note, scheduled_traffic, time_notes, verification_summary
    departure=now_utc()+timedelta(days=1)
    agent=make_agent(tmp_path,mode="driving",departure_time=departure,
        arrival_deadline=departure+timedelta(hours=1))
    agent.plan_candidates();agent.validate_routes([])
    route=next(iter(agent.routes.values()))
    # Stored collection timestamps preserve the reason when viewing old runs.
    context={"retrieved_at":(departure-timedelta(days=1)).isoformat(),
        "collection":{"speed":{"complete":False,"reason":"partial collection"}}}
    assert scheduled_traffic(agent.request,context)
    notes=time_notes(route,context,agent.request)
    assert len(notes)==1 and "预约出发" in notes[0] and "partial collection" not in notes[0]
    assert "50.0 分钟余量" in base_arrival_note(route,agent.request)
    assert "1 项缺少证据：到达时限" in verification_summary(route)
    assert route.total_s is None and route.feasibility=="unverified"


def test_endpoint_evidence_cannot_be_discarded_as_missing():
    text='明早从环球影城回酒店，途中去麦当劳吃早饭停留20分钟'
    with pytest.raises(ToolFailure,match='地点设为空'):
        validate_request(text,{'destination':'酒店','evidence':{'origin':'从环球影城回酒店'}},{})


def test_failed_extraction_retry_cannot_drop_previously_supplied_endpoint(tmp_path):
    agent=make_agent(tmp_path);agent.request=None
    agent.result.request.original_text='从某个景点回酒店，途经一家餐厅'
    first=agent.execute('set_request',json.dumps({'fields':{'origin':'某个景点','destination':'酒店',
        'poi_category':'restaurant','poi_required':True}}),'extract-1')
    assert first['error']=='invalid_evidence' and first['repair_hint']
    second=agent.execute('set_request',json.dumps({'fields':{'destination':'酒店'}}),'extract-2')
    assert second['error']=='invalid_evidence' and '某个景点' in second['message']
    assert not agent.done and not agent.result.questions
    with pytest.raises(ToolFailure) as error:agent.ask_user(['origin'])
    assert error.value.code=='request_required'


def test_single_restaurant_and_breakfast_are_not_multiple_stops():
    text='从环球影城回酒店，途中去麦当劳吃早饭停留20分钟'
    fields={'origin':'环球影城','destination':'酒店','poi_name':'麦当劳','poi_required':True,
        'multiple_stops':True,'stop_duration_s':1200,'evidence':{
        'poi_required':'途中去麦当劳吃早饭','stop_duration_s':'20分钟','multiple_stops':'途中去麦当劳吃早饭'}}
    with pytest.raises(ToolFailure,match='不同经停地点'):validate_request(text,fields,{})
    fields['multiple_stop_evidence']=['麦当劳','去麦当劳吃早饭']
    with pytest.raises(ToolFailure,match='重复引用'):validate_request(text,fields,{})
    fields.update(multiple_stops=False,multiple_stop_evidence=[])
    req,questions=validate_request(text,fields,{})
    assert req.origin=='环球影城' and not questions and not req.multiple_stops


def test_genuine_multiple_business_stops_keep_clarification():
    text='从家到公司，途中去邮局寄信，再去面包店买早餐'
    req,questions=validate_request(text,{'origin':'家','destination':'公司','multiple_stops':True,
        'multiple_stop_evidence':['去邮局寄信','去面包店买早餐']},{})
    assert req.multiple_stops and set(questions)=={'multiple_stops'}
