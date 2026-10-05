"""Protocol/guard tests with mocked providers. NEVER evidence of live connectivity."""
import copy
import json
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from openai.types.chat import ChatCompletionMessageToolCall
from route_agent.config import Settings
from route_agent.models import Place, Evidence, RouteLeg, TravelRequest, PlanningResult, TaskState
from route_agent.tools import Budget, ToolFailure
from route_agent.providers import Providers, now_utc
from route_agent.service import plan_route
from route_agent.coordinator import Agent, tool_schemas
from route_agent.routing import assemble
from route_agent.traffic import apply_traffic
from route_agent.verification import verify_route
from route_agent.parser import validate_request

@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def blocked(*a,**kw):
        raise AssertionError("Online tests must not use real network")
    monkeypatch.setattr(socket.socket,"connect",blocked)
    monkeypatch.setenv("DEEPSEEK_API_KEY","test-secret-deepseek")
    monkeypatch.setenv("ORS_API_KEY","test-secret-ors")
    monkeypatch.delenv("LTA_API_KEY",raising=False)
    monkeypatch.delenv("TAVILY_API_KEY",raising=False)

class FakeProviders:
    def __init__(self):
        self.call_id="test"
        self.cache_hits=0
        self.evidence={}
        self.add("geo","ORS Pelias")
        self.add("osrm","OSRM Route")
        self.add("meteo","Open-Meteo forecast",300)
        self.a=Place(id="origin",name="City Hall",lat=1.29,lon=103.85,source="ORS Pelias",evidence_ids=["geo"])
        self.b=Place(id="destination",name="Marina Bay",lat=1.30,lon=103.86,source="ORS Pelias",evidence_ids=["geo"])
    def add(self,ident,source,ttl=3600):
        self.evidence[ident]=Evidence(id=ident,source=source,retrieved_at=now_utc(),valid_until=now_utc()+timedelta(seconds=ttl),
                                     tool_call_id="mock",response_sha256="mock",summary="MOCK ONLY")
    def geocode(self,query):
        return [self.a if query==self.a.name else self.b]
    def routes(self,a,b,mode,*args):
        return [RouteLeg(id="leg-"+mode+"-"+a.id+"-"+b.id,mode=mode,origin=a,destination=b,
            geometry={"type":"LineString","coordinates":[[a.lon,a.lat],[b.lon,b.lat]]},
            distance_m=1000,provider_duration_s=600,evidence_ids=["osrm"])]
    def weather(self,points=None,departure=None):
        first=now_utc().replace(minute=0,second=0,microsecond=0)
        hours=[{"start":(first+timedelta(hours=n)).isoformat(),"end":(first+timedelta(hours=n+1)).isoformat(),
            "precipitation_probability":20,"precipitation_mm":0,"weather_code":3} for n in range(3)]
        return {"provider":"open_meteo","evidence_ids":["meteo"],"points":[{"requested_coordinate":list(p),
            "grid_coordinate":list(p),"hours":hours} for p in points or [[103.85,1.29],[103.86,1.30]]]}
    def traffic(self):
        raise ToolFailure("缺少 LTA Key","missing_credential")

def request_fields(mode="walking",**kw):
    return {"origin":"City Hall","destination":"Marina Bay","mode":mode,"departure_time":now_utc().isoformat(),**kw}

def make_agent(tmp_path,mode="walking",**kw):
    settings=Settings(root=tmp_path)
    if mode in ("driving","drive_walk"):
        kw.setdefault("car_access",True)  # Fixture represents a confirmed self-driving request.
    req=TravelRequest(original_text="测试需求",**request_fields(mode,**kw))
    result=PlanningResult(run_id="test",status="tool_error",message="",request=req,state=TaskState(request=req))
    agent=Agent(result,settings,Budget(settings),lambda *a,**k:None,providers=FakeProviders())
    agent.request=req
    agent.roles={"origin":agent.providers.a,"destination":agent.providers.b}
    agent.places={p.id:p for p in agent.roles.values()}
    return agent

class Model:
    def __init__(self,steps):
        self.steps=iter(steps); self.seen=[]
        self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self,**kwargs):
        self.seen.append(copy.deepcopy(kwargs))
        step=next(self.steps)
        name,args=step(kwargs["messages"]) if callable(step) else step
        call=ChatCompletionMessageToolCall(id="call-"+str(len(self.seen)),type="function",
            function={"name":name,"arguments":json.dumps(args)})
        msg=SimpleNamespace(content="不应发布的模型猜测 999 公里",reasoning_content="private reasoning must stay internal",tool_calls=[call])
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=10),choices=[SimpleNamespace(message=msg)])

def finish_from_evidence(messages):
    compared=json.loads(messages[-1]["content"])["candidates"][0]
    return "finish_plan",{"selected_id":compared["id"],"evidence_ids":compared["evidence_ids"],"reason_codes":["distance","walking"]}

def test_missing_key_stops_without_model(tmp_path,monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    result=plan_route("去 Marina Bay",settings=Settings(root=tmp_path),save=False)
    assert result.status=="tool_error" and result.metrics["http_calls"]==0
    assert result.context["error_code"]=="missing_credential"

def test_native_tool_loop_preserves_reasoning_but_never_exports_it(tmp_path):
    model=Model([("set_request",{"fields":request_fields()}),("resolve_place",{"role":"origin"}),
        ("resolve_place",{"role":"destination"}),("get_weather",{}),("plan_candidates",{}),
        ("compare_routes",{}),finish_from_evidence])
    result=plan_route("从 City Hall 步行到 Marina Bay",settings=Settings(root=tmp_path),_client=model,_providers=FakeProviders())
    assert result.status=="verified",result.message
    assert result.recommended.total_s==600 and result.recommended.distance_m==1000
    assert len(model.seen)==7 and result.metrics["tool_calls"]==7
    for call in model.seen[1:]:
        assert call["extra_body"]=={"thinking":{"type":"enabled"}}
        assert all(m.get("reasoning_content") for m in call["messages"] if m["role"]=="assistant")
    saved="".join(p.read_text(encoding="utf8") for p in (tmp_path/"outputs"/result.run_id).glob("*"))
    assert "private reasoning" not in saved and "999 公里" not in saved
    assert "test-secret" not in saved

def test_llm_recovers_after_first_poi_fails(tmp_path):
    providers=FakeProviders()
    def pois(center,category,radius,limit,name):
        return [Place(id="poi-"+str(limit),name="Cafe "+str(limit),lat=1.295,lon=103.855,
            category="cafe",source="MOCK OSM",opening_hours="00:00-00:01" if limit==10 else "24/7",evidence_ids=["geo"])]
    providers.pois=pois
    def plan_poi(messages):
        p=json.loads(messages[-1]["content"])["places"][0]
        return "plan_candidates",{"poi_ids":[p["id"]]}
    fields=request_fields(poi_category="cafe",poi_required=True,stop_duration_s=300,require_open=True,
        evidence={"poi_required":"必须经停","stop_duration_s":"5 分钟","require_open":"要求营业"})
    model=Model([("set_request",{"fields":fields}),("resolve_place",{"role":"origin"}),
        ("resolve_place",{"role":"destination"}),("get_weather",{}),("search_pois",{"near":"destination","limit":10}),plan_poi,
        ("compare_routes",{}),("search_pois",{"near":"destination","limit":30}),plan_poi,("compare_routes",{}),finish_from_evidence])
    result=plan_route("从 City Hall 步行到 Marina Bay，必须经停咖啡店，停留 5 分钟，要求营业。",
        settings=Settings(root=tmp_path),_client=model,_providers=providers,save=False)
    assert result.status=="verified",result.message
    assert result.metrics["replans"]==1 and result.recommended.poi.id=="poi-30"
    assert any(event["stage"]=="replan" for event in result.state.stages)

def test_provider_osrm_profiles_and_exact_provider_metrics(tmp_path,monkeypatch):
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    fake=FakeProviders(); seen=[]
    def response(self,method,url,**kwargs):
        seen.append((url,kwargs))
        route={"geometry":{"type":"LineString","coordinates":[[fake.a.lon,fake.a.lat],[fake.b.lon,fake.b.lat]]},
            "distance":1234.5,"duration":678.9,"legs":[]}
        return httpx.Response(200,json={"code":"Ok","routes":[route]},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    walking=provider.routes(fake.a,fake.b,"walking")[0]
    driving=provider.routes(fake.a,fake.b,"driving",True)[0]
    assert walking.distance_m==1234.5 and walking.provider_duration_s==678.9
    assert "/routed-foot/route/v1/foot/" in seen[0][0]
    assert "/routed-car/route/v1/driving/" in seen[1][0]
    assert seen[1][1]["params"]["exclude"]=="motorway" and driving.highways is False
    assert seen[0][1]["params"]["alternatives"]=="2"
    assert seen[0][1]["params"]["overview"]=="full"
    assert "Authorization" not in seen[0][1]["headers"]
    assert walking.source=="OSRM" and provider.evidence[walking.evidence_ids[0]].source=="OSRM Route"

def test_partial_and_stale_traffic_cannot_pass_deadline(tmp_path):
    agent=make_agent(tmp_path,mode="driving",max_duration_s=9999)
    leg=agent.providers.routes(agent.providers.a,agent.providers.b,"driving")[0]
    band={"StartLat":1.29,"StartLon":103.85,"EndLat":1.295,"EndLon":103.855,"MinimumSpeed":20,"MaximumSpeed":40}
    payload={"retrieved_at":now_utc().isoformat(),"speed_bands":[band],"evidence_ids":["osrm"]}
    apply_traffic([leg],payload,now_utc())
    assert 0<leg.traffic_coverage<1 and leg.traffic_duration_s is None
    route=assemble([leg],agent.request,agent.providers.a,agent.providers.b)
    verify_route(route,agent.request,agent.providers.evidence)
    assert next(c for c in route.checks if c.constraint=="duration").status=="unknown"
    route.legs[0].traffic_duration_s=100; route.legs[0].traffic_lower_s=90; route.legs[0].traffic_upper_s=120
    route.legs[0].traffic_retrieved_at=now_utc()-timedelta(hours=1); route.total_s=100
    verify_route(route,agent.request,agent.providers.evidence)
    assert route.total_s is None and route.feasibility=="unverified"

def test_lta_pagination_and_parking_nulls(tmp_path,monkeypatch):
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    monkeypatch.setenv("LTA_API_KEY","test-secret-lta")
    seen=[]
    def response(self,method,url,**kwargs):
        skip=kwargs["params"]["$skip"]; seen.append(skip)
        records=[{"CarParkID":"x","Development":"Test parking","Location":"1.30 103.86","LotType":"C","AvailableLots":0}]
        return httpx.Response(200,json={"value":records*500 if skip==0 else []},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    places=provider.parking(FakeProviders().b)
    assert seen==[0,500] and places[0].available_lots==0
    assert not places[0].entrance_confirmed

def test_authentication_error_does_not_expose_response(tmp_path,monkeypatch):
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    def response(self,method,url,**kwargs):
        return httpx.Response(401,json={"message":"test-secret-ors"},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    with pytest.raises(ToolFailure) as failure:
        provider.geocode("City Hall")
    assert "test-secret" not in str(failure.value) and failure.value.code=="authentication_error"

def test_ambiguous_place_requires_user(tmp_path):
    agent=make_agent(tmp_path)
    agent.providers.geocode=lambda _: [agent.providers.a,agent.providers.b]
    agent.resolve_place("origin")
    assert agent.result.status=="needs_clarification" and "origin_place_id" in agent.result.questions

def test_sealed_request_and_invalid_ids(tmp_path):
    agent=make_agent(tmp_path)
    with pytest.raises(ToolFailure,match="不可"):
        agent.set_request(request_fields())
    assert agent.execute("plan_candidates",'{"poi_ids":["invented"]}',"x")["error"]=="unknown_id"
    assert agent.execute("resolve_place",'{"role":"origin","query":"injected"}',"y")["error"]=="invalid_arguments"
    with pytest.raises(ToolFailure,match="重复"):
        agent.execute("get_weather","{}","y")

def test_unknown_deadline_cannot_publish(tmp_path):
    agent=make_agent(tmp_path,mode="driving",max_duration_s=1000)
    agent.traffic_attempted=True
    agent.plan_candidates()
    ident=next(iter(agent.routes))
    with pytest.raises(ToolFailure,match="不能发布"):
        agent.finish_plan(ident,evidence_ids=["osrm"])
    agent.finish_plan(None)
    assert agent.result.status=="unverified" and agent.result.unverified[0].total_s is None

@pytest.mark.parametrize("mode",["walking","driving"])
def test_unconstrained_trip_can_finish_without_unrelated_information_tools(tmp_path,mode):
    agent=make_agent(tmp_path,mode=mode)
    agent.plan_candidates()
    route=next(iter(agent.routes.values()))
    assert route.weather is None
    agent.finish_plan(route.id,evidence_ids=["osrm"],reason_codes=["distance"])
    assert agent.result.status=="verified"
    assert not agent.traffic_attempted and not agent.weather_attempted
    if mode=="driving":
        assert agent.result.recommended.total_s is None
        assert agent.result.recommended.provider_total_s==600

@pytest.mark.parametrize("requirement",[
    {"max_duration_s":1000}, {"arrival_deadline":now_utc()+timedelta(hours=1)},
    {"prefer_avoid_congestion":True}, {"max_congestion_fraction":.2}, {"require_open":True},
])
def test_driving_relevant_requirements_still_require_traffic_attempt(tmp_path,requirement):
    agent=make_agent(tmp_path,mode="driving",**requirement)
    agent.plan_candidates()
    with pytest.raises(ToolFailure) as failure:
        agent.finish_plan(None)
    assert failure.value.code=="missing_evidence" and "LTA" in str(failure.value)

@pytest.mark.parametrize("requirement",[
    {"weather_required":True}, {"prefer_avoid_rain":True}, {"require_dry":True},
])
def test_explicit_weather_requirements_still_require_weather_attempt(tmp_path,requirement):
    agent=make_agent(tmp_path,**requirement)
    agent.plan_candidates()
    with pytest.raises(ToolFailure) as failure:
        agent.finish_plan(next(iter(agent.routes)),evidence_ids=["osrm"])
    assert failure.value.code=="missing_evidence" and "天气" in str(failure.value)

def test_native_agent_chooses_a_shorter_tool_sequence_for_plain_walking(tmp_path):
    model=Model([("set_request",{"fields":request_fields()}),("resolve_place",{"role":"origin"}),
        ("resolve_place",{"role":"destination"}),("plan_candidates",{}),
        ("compare_routes",{}),finish_from_evidence])
    result=plan_route("从 City Hall 步行到 Marina Bay",settings=Settings(root=tmp_path),
        _client=model,_providers=FakeProviders(),save=False)
    assert result.status=="verified",result.message
    assert {call.tool for call in result.state.tools}=={
        "set_request","resolve_place","plan_candidates","compare_routes","finish_plan"}
    assert result.recommended.weather is None

@pytest.mark.parametrize("code,expected",[("unsupported_feature","tool_error"),("no_route","search_exhausted")])
def test_route_tool_failure_is_not_misreported_as_search_exhaustion(tmp_path,code,expected):
    agent=make_agent(tmp_path)
    def unavailable(*a,**kw):
        raise ToolFailure("MOCK OSRM failure",code)
    agent.providers.routes=unavailable
    assert agent.execute("plan_candidates","{}","mock-plan")["error"]==code
    assert agent.execute("finish_plan","{}","mock-finish")["status"]==expected
    assert agent.result.status==expected and agent.result.recommended is None
    if code=="unsupported_feature":
        assert agent.result.message=="MOCK OSRM failure"
        assert agent.result.context["error_code"]==code

def test_required_dry_and_opening_unknown(tmp_path):
    agent=make_agent(tmp_path,require_dry=True,require_open=True)
    agent.plan_candidates()
    route=next(iter(agent.routes.values()))
    verify_route(route,agent.request,agent.providers.evidence)
    assert route.feasibility=="unverified"
    assert {c.constraint for c in route.checks if c.status=="unknown"}=={"dry_weather","opening_hours"}

def test_publication_evidence_and_no_hiding_verified(tmp_path):
    agent=make_agent(tmp_path)
    agent.weather_attempted=True; agent.plan_candidates()
    ident=next(iter(agent.routes))
    with pytest.raises(ToolFailure,match="每段"):
        agent.finish_plan(ident,evidence_ids=["geo"])
    with pytest.raises(ToolFailure,match="方案"):
        agent.finish_plan(None)
    with pytest.raises(ToolFailure,match="证据"):
        agent.finish_plan(ident,evidence_ids=["invented"])
    with pytest.raises(ToolFailure,match="设施"):
        agent.finish_plan(ident,evidence_ids=["osrm"],reason_codes=["parking"])

def test_budget_caps_and_no_rule_fallback(tmp_path):
    settings=Settings(root=tmp_path,max_http_calls=1,max_tool_calls=1,max_rounds=1)
    budget=Budget(settings); budget.http(); budget.tool()
    for method in (budget.http,budget.tool):
        with pytest.raises(ToolFailure): method()
    model=Model([("set_request",{"fields":request_fields()})])
    result=plan_route("测试",settings=settings,_client=model,_providers=FakeProviders(),save=False)
    assert result.status=="tool_error" and result.context["error_code"]=="budget_exhausted"

def test_model_error_stops_without_fallback(tmp_path):
    class Broken:
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:(_ for _ in ()).throw(RuntimeError("test-secret-deepseek"))))
    result=plan_route("测试",settings=Settings(root=tmp_path),_client=Broken(),_providers=FakeProviders(),save=False)
    assert result.status=="tool_error" and result.context["error_code"]=="model_error"
    assert "test-secret" not in result.model_dump_json()

def test_replan_gates_and_limit(tmp_path):
    agent=make_agent(tmp_path,max_walking_m=0,poi_category="cafe",stop_duration_s=0,poi_required=True)
    with pytest.raises(ToolFailure,match="首批"):
        agent.search_pois(limit=30)
    for n in range(3):
        poi=Place(id="poi"+str(n),name="Cafe",lat=1.295,lon=103.855+n*.001,category="cafe",source="OSM",evidence_ids=["geo"])
        agent.places[poi.id]=poi
        if n:
            with pytest.raises(ToolFailure,match="验证"):
                agent.plan_candidates([poi.id])
            agent.validate_routes()
        agent.plan_candidates([poi.id])
    agent.validate_routes()
    with pytest.raises(ToolFailure,match="上限"):
        agent.plan_candidates([poi.id])

def test_numeric_extraction_is_validated():
    with pytest.raises(ToolFailure,match="单位"):
        validate_request("最多 30 分钟",request_fields(max_duration_s=30,evidence={"max_duration_s":"30 分钟"}),{})
    req,_=validate_request("最多 30 分钟",request_fields(max_duration_s=1800,evidence={"max_duration_s":"30 分钟"}),{})
    assert req.max_duration_s==1800
    req,qs=validate_request("驾车后步行",request_fields(mode="drive_walk"),{})
    assert "parking_duration_s" in qs

def test_unspecified_mode_and_time_do_not_force_frontend_selection():
    req,questions=validate_request("从 City Hall 到 Marina Bay，帮我选合适的方式",
        {"origin":"City Hall","destination":"Marina Bay"},{})
    assert req.mode is None and req.departure_time is not None
    assert "mode" not in questions and "departure_time" not in questions

def test_trusted_user_correction_is_not_rejected_for_model_evidence_text():
    req,_=validate_request("经过咖啡店",{"origin":"City Hall","destination":"Marina Bay",
        "stop_duration_s":100,"evidence":{"stop_duration_s":"用户刚补充的 5 分钟"}},
        {"stop_duration_s":300})
    assert req.stop_duration_s==300

def test_model_selects_transport_after_real_tool_comparison(tmp_path):
    def choose_from_comparison(messages):
        comparison=json.loads(messages[-1]["content"])
        walking=next(o for o in comparison["options"] if o["mode"]=="walking")
        return "choose_mode",{"mode":"walking","evidence_ids":walking["evidence_ids"],"reason_codes":["distance"]}
    model=Model([("set_request",{"fields":request_fields(mode=None)}),("resolve_place",{"role":"origin"}),
        ("resolve_place",{"role":"destination"}),("compare_modes",{}),choose_from_comparison,
        ("plan_candidates",{}),("compare_routes",{}),finish_from_evidence])
    result=plan_route("从 City Hall 到 Marina Bay，请帮我判断出行方式",settings=Settings(root=tmp_path),
        _client=model,_providers=FakeProviders(),save=False)
    assert result.status=="verified",result.message
    assert result.request.mode is None and result.recommended.mode=="walking" and not result.questions
    assert "osrm" in result.context["mode_selection"]["evidence_ids"]
    assert {o["mode"] for o in result.context["mode_comparison"]["options"]}=={"walking","driving"}

def test_mode_decision_cannot_override_user_or_use_invented_evidence(tmp_path):
    explicit=make_agent(tmp_path,mode="walking")
    with pytest.raises(ToolFailure) as error:
        explicit.compare_modes()
    assert error.value.code=="immutable_request"
    agent=make_agent(tmp_path,mode=None)
    agent.compare_modes()
    with pytest.raises(ToolFailure) as error:
        agent.choose_mode("walking",["invented"],["distance"])
    assert error.value.code=="invalid_evidence"
    agent.choose_mode("driving",["osrm"],["time"])
    assert "car_access" in agent.result.questions
    assert agent.request.mode is None
    agent.done=False
    agent.request.car_access=True
    agent.choose_mode("driving",["osrm"],["time"])
    assert agent.request.mode is None and agent.result.context["mode_selection"]["car_access_confirmed"]
    agent.choose_mode("walking",["osrm"],["distance"])
    assert agent.request.mode is None and agent.result.context["mode_selection"]["provisional"]

def test_lta_pagination_reads_beyond_old_40_page_limit(tmp_path,monkeypatch):
    settings=Settings(root=tmp_path)
    provider=Providers(settings,Budget(settings))
    monkeypatch.setenv("LTA_API_KEY","test-secret-lta")
    skips=[]
    def response(self,method,url,**kwargs):
        skip=kwargs["params"]["$skip"]; skips.append(skip)
        rows=[{"LinkID":str(skip+i)} for i in range(500 if skip<20500 else 7)]
        return httpx.Response(200,json={"value":rows},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    rows,refs=provider.lta("v4/TrafficSpeedBands",300)
    assert len(rows)==20507 and len(refs)==42 and skips[-1]==20500
    assert provider.lta_status["v4/TrafficSpeedBands"]["complete"] is True

def test_lta_partial_pages_remain_real_and_are_labelled_with_budget_reason(tmp_path,monkeypatch):
    from dataclasses import replace
    settings=replace(Settings(root=tmp_path),max_http_calls=4)
    provider=Providers(settings,Budget(settings))
    monkeypatch.setenv("LTA_API_KEY","test-secret-lta")
    def response(self,method,url,**kwargs):
        skip=kwargs["params"]["$skip"]
        return httpx.Response(200,json={"value":[{"LinkID":str(skip+i)} for i in range(500)]},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    rows,refs=provider.lta("v4/TrafficSpeedBands",300,reserve_http=2,allow_partial=True)
    status=provider.lta_status["v4/TrafficSpeedBands"]
    assert len(rows)==1000 and len(refs)==2 and provider.budget.http_calls==2
    assert status["complete"] is False and "预算" in status["reason"]

def test_lta_repeated_page_is_detected_instead_of_infinite_paging(tmp_path,monkeypatch):
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    monkeypatch.setenv("LTA_API_KEY","test-secret-lta")
    def response(self,method,url,**kwargs):
        return httpx.Response(200,json={"value":[{"LinkID":str(i)} for i in range(500)]},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    with pytest.raises(ToolFailure) as error:
        provider.lta("v4/TrafficSpeedBands",300)
    assert error.value.code=="repeated_page" and provider.budget.http_calls==2

def test_request_schema_documents_fields():
    schema=tool_schemas()[0]["function"]["parameters"]["properties"]["fields"]
    assert "max_walking_m" in schema["properties"] and "original_text" not in schema["properties"]

def test_provider_retries_cache_auth_and_pagination(tmp_path,monkeypatch):
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    seen=[]
    def reply(self,method,url,**kwargs):
        seen.append((url,kwargs))
        return httpx.Response(429 if len(seen)<3 else 200,json={"value":[]},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",reply)
    monkeypatch.setattr("route_agent.providers.time.sleep",lambda _:None)
    monkeypatch.setenv("LTA_API_KEY","test-secret-lta")
    rows,refs=provider.lta("CarParkAvailabilityv2",60)
    assert rows==[] and len(seen)==3 and provider.budget.http_calls==3
    assert seen[-1][0].endswith("/CarParkAvailabilityv2")
    assert seen[-1][1]["headers"]["AccountKey"]=="test-secret-lta"
    provider.lta("CarParkAvailabilityv2",60)
    assert len(seen)==3 and provider.cache_hits==1
    cache="".join(p.read_text(encoding="utf8") for p in (tmp_path/"data/cache/online-v2").glob("*.json"))
    assert "test-secret-lta" not in cache

def test_traffic_direction_future_and_zero_speed(tmp_path):
    agent=make_agent(tmp_path,mode="driving")
    leg=agent.providers.routes(agent.providers.a,agent.providers.b,"driving")[0]
    band={"StartLat":1.29,"StartLon":103.85,"EndLat":1.30,"EndLon":103.86,"MinimumSpeed":20,"MaximumSpeed":40}
    payload={"retrieved_at":now_utc().isoformat(),"speed_bands":[band],"evidence_ids":[]}
    apply_traffic([leg],payload,now_utc())
    assert leg.traffic_coverage==pytest.approx(1) and leg.traffic_upper_s==pytest.approx(180)
    apply_traffic([leg],payload,now_utc()+timedelta(hours=1))
    assert leg.traffic_duration_s is None and leg.traffic_coverage==0
    apply_traffic([leg],payload,now_utc()+timedelta(minutes=5))
    assert leg.traffic_duration_s is None
    payload["speed_bands"]=[{**band,"StartLat":1.30,"StartLon":103.86,"EndLat":1.29,"EndLon":103.85}]
    apply_traffic([leg],payload,now_utc())
    assert leg.traffic_coverage==0
    payload["speed_bands"]=[{**band,"MinimumSpeed":0}]
    apply_traffic([leg],payload,now_utc())
    assert leg.traffic_lower_s is not None and leg.traffic_upper_s is None

def test_walking_hybrid_totals_and_parking_guards(tmp_path):
    agent=make_agent(tmp_path,mode="drive_walk",parking_duration_s=120)
    p=Place(id="park",name="Car park",lat=1.295,lon=103.855,category="parking",source="LTA",available_lots=5,evidence_ids=["geo"])
    legs=[agent.providers.routes(agent.providers.a,p,"driving")[0],agent.providers.routes(p,agent.providers.b,"walking")[0]]
    legs[0].traffic_duration_s=100; legs[0].traffic_lower_s=90; legs[0].traffic_upper_s=120; legs[0].traffic_coverage=1
    route=assemble(legs,agent.request,agent.providers.a,agent.providers.b,parking=p)
    assert route.total_s==820 and route.provider_total_s==1320 and route.walking_m==1000
    verify_route(route,agent.request,agent.providers.evidence)
    assert route.feasibility=="unverified"
    p.entrance_confirmed=True; route.parking.entrance_confirmed=True
    verify_route(route,agent.request,agent.providers.evidence)
    assert route.feasibility=="verified"
    route.parking.available_lots=0
    verify_route(route,agent.request,agent.providers.evidence)
    assert route.feasibility=="violated"

def test_forecast_validity_and_segment_gaps(tmp_path):
    agent=make_agent(tmp_path)
    agent.plan_candidates(); route=next(iter(agent.routes.values()))
    agent.weather=agent.providers.weather()
    assert agent.weather_for(route).status=="available"
    agent.request.departure_time=now_utc()+timedelta(hours=4)
    assert agent.weather_for(route).status=="unknown"
    second=route.legs[0].model_copy(deep=True)
    second.geometry["coordinates"][0]=[103.9,1.35]
    route.legs.append(second)
    verify_route(route,agent.request)
    assert next(c for c in route.checks if c.constraint=="continuity").status=="unknown"

@pytest.mark.parametrize("query",["滨海湾金沙（Marina Bay Sands）","Marina Bay Sands"])
def test_geocode_uses_supplied_alias_and_rejects_other_landmarks(tmp_path,monkeypatch,query):
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    seen=[]
    def request(*a,**kwargs):
        seen.append(kwargs["params"]["text"])
        names=["Gardens by the Bay","Marina Bay","Marina Bay Suites","Marina Bay Sands"]
        return {"features":[{"geometry":{"coordinates":[103.86,1.28]},"properties":{
            "gid":name,"name":name,"label":name+", Singapore","confidence":1.0}} for name in names]},"actual-shaped-evidence"
    monkeypatch.setattr(provider,"request",request)
    places=provider.geocode(query)
    assert seen==["Marina Bay Sands"]
    assert [p.name for p in places]==["Marina Bay Sands, Singapore"]
    assert places[0].evidence_ids==["actual-shaped-evidence"]

def test_geocode_no_name_match_returns_no_selectable_substitute(tmp_path,monkeypatch):
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    monkeypatch.setattr(provider,"request",lambda *a,**k:({"features":[{"geometry":{"coordinates":[103.86,1.28]},
        "properties":{"gid":"wrong","name":"Gardens by the Bay","confidence":1.0}}]},"ev"))
    assert provider.geocode("Marina Bay Sands")==[]

def test_location_matching_supports_addresses_and_broad_ambiguity():
    from route_agent.providers import location_query,location_name_matches
    assert location_query("新加坡市政厅（City Hall）")=="City Hall"
    assert location_name_matches("10 Bayfront Avenue","10 Bayfront Avenue, Singapore")
    assert location_name_matches("Marina Bay","Marina Bay Sands")
    assert not location_name_matches("Marina Bay Sands","Gardens by the Bay, Marina South")
    assert not location_name_matches("Hall","Hallmark Tower")

@pytest.mark.parametrize("query,name,expected",[
    ("Woodland Checkpoint","Woodlands Checkpoint",True),
    ("Marina Bay Sand","Marina Bay Sands",False),  # Short words are not fuzzy matches.
    ("Chinatwon","Chinatown",True),
    ("Checkpoint Woodlands","Woodlands Checkpoint",True),
    ("Marina Bay Sands","Marina Bay Suites",False),
    ("Marina Bay Sands","Gardens by the Bay, Marina South",False),
    ("Hall","Hallmark Tower",False),
    ("10 Bayfront Avenue","11 Bayfront Avenue",False),
    ("Bay Bay","Marina Bay",False),
])
def test_conservative_fuzzy_names(query,name,expected):
    from route_agent.providers import location_name_similar
    assert location_name_similar(query,name)==expected

def test_high_confidence_typo_hit_still_requires_confirmation(tmp_path,monkeypatch):
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    monkeypatch.setattr(provider,"request",lambda *a,**k:({"features":[{"geometry":{"coordinates":[103.84,1.28]},
        "properties":{"gid":"chinatown","name":"Chinatown","confidence":1.0}}]},"ev"))
    places=provider.geocode("Chinatwon")
    assert len(places)==1 and places[0].requires_confirmation
    assert places[0].evidence_ids==["ev"]

def test_checkpoint_compound_retry_does_not_replace_landmark_with_neighbourhood(tmp_path,monkeypatch):
    provider=Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))
    seen=[]
    def request(*args,**kwargs):
        query=kwargs["params"]["text"]
        seen.append(query)
        name="Woodlands" if query=="Woodlands Checkpoint" else "Woodlands Checkpoint"
        return {"features":[{"geometry":{"coordinates":[103.77,1.44]},
            "properties":{"gid":name,"name":name,"confidence":1.0}}]},"ev-"+str(len(seen))
    monkeypatch.setattr(provider,"request",request)
    places=provider.geocode("Woodlands Checkpoint")
    assert seen==["Woodlands Checkpoint","Woodlands check point"]
    assert len(places)==1 and places[0].name=="Woodlands Checkpoint"
    assert places[0].requires_confirmation and places[0].evidence_ids==["ev-2"]

def test_descriptive_origin_searches_real_candidates_and_preserves_wording(tmp_path):
    agent=make_agent(tmp_path)
    description="马来西亚新山入境新加坡的口岸"
    agent.request.origin=description
    seen=[]
    def geocode(query):
        seen.append(query)
        return {"Woodlands Checkpoint":[agent.providers.a],"Tuas Checkpoint":[agent.providers.b]}.get(query,[])
    agent.providers.geocode=geocode
    agent.roles.clear()
    agent.resolve_place("origin",["Woodlands Checkpoint","Tuas Checkpoint"])
    assert seen==[description,"Woodlands Checkpoint","Tuas Checkpoint"]
    assert agent.request.origin==description
    assert "origin" not in agent.roles
    assert agent.result.status=="needs_clarification"
    assert len(agent.choices["origin_place_id"])==2
    assert all(agent.places[x["id"]].requires_confirmation for x in agent.choices["origin_place_id"])

def test_alias_confirmation_replays_saved_searches_and_deduplicates(tmp_path):
    agent=make_agent(tmp_path)
    agent.request.destination="唐人街"
    place=agent.providers.b
    seen=[]
    def geocode(query):
        seen.append(query)
        return [place.model_copy(deep=True)] if query in ("Chinatown","Kreta Ayer") else []
    agent.providers.geocode=geocode
    agent.roles.pop("destination")
    agent.resolve_place("destination",["Chinatown","Kreta Ayer","Chinatown"])
    assert len(agent.choices["destination_place_id"])==1
    assert "destination" not in agent.roles
    saved=copy.deepcopy(agent.result.context["place_searches"])
    agent.done=False
    agent.clarifications={"destination_place_id":place.id,"place_searches":saved}
    output=agent.resolve_place("destination")
    assert seen==["唐人街","Chinatown","Kreta Ayer"]*2
    assert agent.roles["destination"].id==place.id
    assert output["user_confirmed"] and not output["resolved"]["requires_confirmation"]
    # Replacing the location cannot reuse an old alias or confirmed ID.
    agent.request.destination="City Hall"
    agent.clarifications.pop("destination_place_id")
    agent.resolve_place("destination")
    assert seen[-1]=="City Hall"

def test_empty_direct_geocode_allows_semantic_retry_but_not_unrelated_substitute(tmp_path):
    agent=make_agent(tmp_path)
    agent.providers.geocode=lambda _: []
    output=agent.resolve_place("origin")
    assert output["retry_with_search_queries"] and not agent.done
    output=agent.resolve_place("origin",["City Hall"])
    assert not agent.done and output["resolved"] is None
    agent.ask_user(["origin"])
    assert agent.done and agent.result.status=="needs_clarification"
    assert "origin" in agent.result.questions

def test_semantic_query_schema_is_bounded_and_rejects_generated_coordinates(tmp_path):
    agent=make_agent(tmp_path)
    for index,queries in enumerate((["x"]*4,["x"*161],["1.29,103.85"])):
        output=agent.execute("resolve_place",json.dumps({"role":"origin","search_queries":queries}),"search-"+str(index))
        assert output["error"]=="invalid_arguments"

def test_native_ask_user_is_executable_and_ends_loop(tmp_path):
    agent=make_agent(tmp_path)
    output=agent.execute("ask_user",json.dumps({"fields":["travel_preferences"]}),"ask-native")
    assert "questions" in output and agent.done
    assert agent.result.status=="needs_clarification"

def test_model_cannot_ask_again_for_a_resolved_endpoint(tmp_path):
    agent=make_agent(tmp_path)
    output=agent.execute("ask_user",json.dumps({"fields":["origin"]}),"repeat-origin")
    assert output["error"]=="already_resolved" and not agent.done

def test_unconfirmed_reresolution_removes_old_route_endpoint(tmp_path):
    agent=make_agent(tmp_path)
    agent.providers.geocode=lambda _: [agent.providers.a,agent.providers.b]
    agent.resolve_place("origin")
    assert "origin" not in agent.roles and agent.done

def test_schema_error_feedback_names_missing_fields_without_raw_input(tmp_path):
    agent=make_agent(tmp_path)
    output=agent.execute("set_request",json.dumps({"evidence":{"private":"secret-string"}}),"invalid-shape")
    assert {"field":"fields","type":"missing"} in output["argument_issues"]
    assert "secret-string" not in json.dumps(output)
