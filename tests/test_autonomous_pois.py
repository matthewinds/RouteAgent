"""Generic stops are searched and compared by the agent, not supplied by users."""
import json
import socket
import pytest
from route_agent.config import Settings
from route_agent.models import Place
from route_agent.parser import validate_request
from route_agent.providers import Providers
from route_agent.service import plan_route
from route_agent.tools import Budget,ToolFailure
from test_online import FakeProviders,Model,make_agent,request_fields,finish_from_evidence


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket,"connect",lambda *a,**k:pytest.fail("No real network in regression tests"))
    monkeypatch.setenv("DEEPSEEK_API_KEY","mock")
    monkeypatch.setenv("ORS_API_KEY","mock")


def test_bakery_category_needs_only_missing_duration_not_address():
    req,questions=validate_request("从 A 到 B，经过一家面包店买面包",{
        "origin":"A","destination":"B","poi_category":"bakery","poi_required":True,
        "evidence":{"poi_required":"经过一家面包店"}}, {})
    assert req.poi_category=="bakery" and req.poi_name is None
    assert set(questions)=={"stop_duration_s"}


@pytest.mark.parametrize("category",["amenity:fuel","shop:supermarket","amenity:pharmacy","leisure:fitness_centre","tourism:museum","healthcare:physiotherapist"])
def test_open_stop_categories_are_autonomous_not_missing_addresses(category):
    req,questions=validate_request("途中办事",{"origin":"A","destination":"B",
        "poi_category":category,"poi_required":True,"evidence":{"poi_required":"途中办事"}}, {})
    assert req.poi_category==category and req.poi_name is None
    assert set(questions)=={"stop_duration_s"}


def test_refuelling_extraction_cannot_end_with_missing_stop_address(tmp_path):
    agent=make_agent(tmp_path);agent.request=None
    fields=request_fields(mode="driving",poi_required=True,evidence={"poi_required":"途中加个油"})
    agent.result.request.original_text="途中加个油"
    with pytest.raises(ToolFailure,match="经停需求尚未提取完整"):
        agent.set_request(fields)
    assert not agent.done and not agent.result.questions and agent.request is None


@pytest.mark.parametrize("category",["amenity:fuel","shop:supermarket","healthcare:physiotherapist"])
def test_generic_osm_tags_search_and_validate_actual_candidate_type(tmp_path,monkeypatch,category):
    settings=Settings(root=tmp_path);provider=Providers(settings,Budget(settings))
    key,value=category.split(":")
    center=Place(id="a",name="A",lat=1.29,lon=103.85,source="MOCK")
    seen=[]
    def overpass(query,source):
        seen.append(query)
        return {"elements":[{"type":"node","id":1,"lat":1.295,"lon":103.855,
            "tags":{key:value,"name":"Matching stop"}},
            {"type":"node","id":2,"lat":1.295,"lon":103.855,
            "tags":{key:"restaurant","name":"Wrong type"}}]},"ev"
    monkeypatch.setattr(provider,"overpass",overpass)
    places=provider.pois(center,category)
    assert len(places)==1 and places[0].category==category and places[0].id=="osm:node:1"
    assert f'["{key}"="{value}"]' in seen[0]
    agent=make_agent(tmp_path,poi_category=category,poi_required=True,stop_duration_s=300)
    for field in ["poi_name","poi_category","poi_place_id"]:
        with pytest.raises(ToolFailure) as failure:agent.ask_user([field])
        assert failure.value.code=="autonomous_search_required"


@pytest.mark.parametrize("category",['amenity:fuel\"];out;','building:office','shop:','amenity:fuel;restaurant'])
def test_general_category_rejects_unbounded_or_injected_queries(tmp_path,category):
    settings=Settings(root=tmp_path);provider=Providers(settings,Budget(settings))
    center=Place(id="a",name="A",lat=1.29,lon=103.85,source="MOCK")
    with pytest.raises(ToolFailure):provider.pois(center,category)


@pytest.mark.parametrize("category",["amenity:fuel","shop:supermarket"])
def test_agent_routes_through_general_stop_without_asking_user_to_select_it(tmp_path,category):
    providers=FakeProviders()
    places=[Place(id="stop-a",name="Station A",lat=1.295,lon=103.855,
        category=category,source="MOCK OSM",evidence_ids=["geo"]),
        Place(id="stop-b",name="Station B",lat=1.296,lon=103.856,
        category=category,source="MOCK OSM",evidence_ids=["geo"])]
    providers.pois_along_route=lambda *args:places
    def plan_stops(messages):
        choices=json.loads(messages[-1]["content"])["places"]
        return "plan_candidates",{"poi_ids":[p["id"] for p in choices]}
    def finish_stop(messages):
        candidate=json.loads(messages[-1]["content"])["candidates"][0]
        return "finish_plan",{"selected_id":candidate["id"],"reason_codes":["distance","poi"]}
    fields=request_fields(mode="driving",car_access=True,poi_category=category,
        poi_required=True,stop_duration_s=300,evidence={"car_access":"开车",
            "poi_required":"途中办事","stop_duration_s":"5分钟"})
    model=Model([("set_request",{"fields":fields}),("resolve_place",{"role":"origin"}),
        ("resolve_place",{"role":"destination"}),("ask_user",{"fields":["poi_name"]}),
        ("search_pois",{}),plan_stops,("compare_routes",{}),finish_stop])
    result=plan_route("开车从 City Hall 到 Marina Bay，途中办事，停留5分钟。",
        settings=Settings(root=tmp_path),_providers=providers,_client=model,save=False)
    assert result.recommended and result.recommended.poi.category==category
    assert not result.questions and len(result.state.candidates)>=2
    assert any(t.tool=="ask_user" and t.status=="error" for t in result.state.tools)


@pytest.mark.parametrize("name",["bakery","面包点","coffee shop","医院"])
def test_generic_category_cannot_be_extracted_as_specific_store(name):
    with pytest.raises(ToolFailure) as failure:
        validate_request("经过一家店",{"poi_name":name}, {})
    assert failure.value.code=="invalid_evidence" and "search_pois" in str(failure.value)


def test_specific_named_bakery_is_preserved():
    req,_=validate_request("去 BreadTalk 买面包停留5分钟",{
        "poi_name":"BreadTalk","poi_category":"bakery","stop_duration_s":300,
        "evidence":{"stop_duration_s":"5分钟"}}, {})
    assert req.poi_name=="BreadTalk" and req.poi_category=="bakery"


def test_generic_stop_rejects_geocoding_and_unnecessary_user_selection(tmp_path):
    agent=make_agent(tmp_path,poi_category="bakery",poi_required=True,stop_duration_s=300)
    for call in (lambda:agent.resolve_place("poi"),lambda:agent.ask_user(["poi_place_id"]),
                 lambda:agent.ask_user(["poi_name"]),lambda:agent.ask_user(["poi_category"])):
        with pytest.raises(ToolFailure) as failure:call()
        assert failure.value.code=="autonomous_search_required"
    assert not agent.done and not agent.result.questions
    agent.ask_user(["car_access"])
    assert agent.result.questions.keys()=={"car_access"}


def test_real_osm_bakery_tag_and_route_samples_are_searched(tmp_path,monkeypatch):
    settings=Settings(root=tmp_path); provider=Providers(settings,Budget(settings))
    fake=FakeProviders(); provider.evidence.update(fake.evidence)
    reference=fake.routes(fake.a,fake.b,"walking")[0]
    reference.geometry["coordinates"]=[[103.85,1.29],[103.852,1.292],[103.855,1.295],[103.858,1.298],[103.86,1.30]]
    seen=[]
    elements=[{"type":"node","id":1,"lat":1.295,"lon":103.855,"tags":{"shop":"bakery","name":"Bread shop","opening_hours":"24/7"}},
        {"type":"node","id":2,"lat":1.295,"lon":103.855,"tags":{"amenity":"cafe","name":"Cafe with cake"}}]
    def overpass(query,source):seen.append(query);return {"elements":elements},"geo"
    monkeypatch.setattr(provider,"overpass",overpass)
    places=provider.pois_along_route(reference,"bakery")
    assert len(places)==1 and places[0].category=="bakery" and places[0].id=="osm:node:1"
    assert places[0].opening_hours=="24/7" and "osrm" in places[0].evidence_ids
    assert len(seen)==1 and '["shop"="bakery"]' in seen[0] and '["amenity"="bakery"]' not in seen[0]
    assert "1.295,103.855" in seen[0] and seen[0].count("nwr(")<=5
    reference.evidence_ids=[]
    with pytest.raises(ToolFailure):provider.pois_along_route(reference,"bakery")


def test_agent_compares_bakeries_and_publishes_only_candidate_meeting_deadline(tmp_path):
    providers=FakeProviders()
    places=[Place(id="near-bakery",name="Near bakery",lat=1.295,lon=103.855,category="bakery",source="MOCK OSM",evidence_ids=["geo"]),
        Place(id="far-bakery",name="Far bakery",lat=1.32,lon=103.88,category="bakery",source="MOCK OSM",evidence_ids=["geo"])]
    seen=[]
    def along(reference,category,radius,limit,name):
        seen.append((reference,category,name));return places
    providers.pois_along_route=along
    routes=providers.routes
    def timed(a,b,mode,*args):
        legs=routes(a,b,mode,*args)
        if "far-bakery" in (a.id,b.id):legs[0].provider_duration_s=2000
        return legs
    providers.routes=timed
    def plan_stops(messages):
        choices=json.loads(messages[-1]["content"])["places"]
        return "plan_candidates",{"poi_ids":[p["id"] for p in choices]}
    fields=request_fields(poi_category="bakery",poi_required=True,stop_duration_s=300,max_duration_s=1800,
        evidence={"poi_required":"经过一家面包店","stop_duration_s":"5分钟","max_duration_s":"30分钟"})
    model=Model([("set_request",{"fields":fields}),("resolve_place",{"role":"origin"}),
        ("resolve_place",{"role":"destination"}),("resolve_place",{"role":"poi"}),
        ("ask_user",{"fields":["poi_place_id"]}),("search_pois",{}),plan_stops,
        ("compare_routes",{}),finish_from_evidence])
    result=plan_route("从 City Hall 步行到 Marina Bay，经过一家面包店买面包，停留5分钟，30分钟内到达。",
        settings=Settings(root=tmp_path),_providers=providers,_client=model,save=False)
    assert result.status=="verified",result.message
    assert not result.questions and result.recommended.poi.id=="near-bakery"
    assert result.recommended.total_s==1500
    assert any(r.poi.id=="far-bakery" and r.feasibility=="violated" for r in result.state.candidates)
    assert seen[0][1:]==("bakery",None) and result.context["poi_searches"][0]["near"]=="route"


def test_empty_search_can_expand_without_asking_for_store_address(tmp_path):
    agent=make_agent(tmp_path,poi_category="bakery",poi_required=True,stop_duration_s=300)
    seen=[]
    def along(*args):seen.append(args[3]);return []
    agent.providers.pois_along_route=along
    assert not agent.search_pois()["places"]
    agent.search_pois(radius_m=2000,limit=30)
    assert seen==[10,30] and not agent.result.questions


def test_closed_bakery_triggers_new_search_and_replanning(tmp_path):
    providers=FakeProviders()
    def along(reference,category,radius,limit,name):
        return [Place(id="bakery-"+str(limit),name="Bakery "+str(limit),lat=1.295,lon=103.855,
            category="bakery",opening_hours="00:00-00:01" if limit==10 else "24/7",source="MOCK OSM",evidence_ids=["geo"])]
    providers.pois_along_route=along
    def plan_stops(messages):
        return "plan_candidates",{"poi_ids":[p["id"] for p in json.loads(messages[-1]["content"])["places"]]}
    fields=request_fields(poi_category="bakery",poi_required=True,stop_duration_s=300,require_open=True,
        evidence={"poi_required":"经过一家面包店","stop_duration_s":"5分钟","require_open":"必须营业"})
    model=Model([("set_request",{"fields":fields}),("resolve_place",{"role":"origin"}),
        ("resolve_place",{"role":"destination"}),("search_pois",{}),plan_stops,("compare_routes",{}),
        ("search_pois",{"radius_m":2000,"limit":30}),plan_stops,("compare_routes",{}),finish_from_evidence])
    result=plan_route("City Hall 步行去 Marina Bay，经过一家面包店买面包，停留5分钟，必须营业。",
        settings=Settings(root=tmp_path),_providers=providers,_client=model,save=False)
    assert result.status=="verified" and result.recommended.poi.id=="bakery-30"
    assert result.metrics["replans"]==1 and not result.questions


def test_unreachable_store_does_not_discard_other_real_candidates(tmp_path):
    agent=make_agent(tmp_path,poi_category="bakery",poi_required=True,stop_duration_s=300)
    for ident in ("unreachable","reachable"):
        agent.places[ident]=Place(id=ident,name=ident,lat=1.295,lon=103.855,category="bakery",source="MOCK",evidence_ids=["geo"])
    original=agent.providers.routes
    def routes(a,b,mode,*args):
        if "unreachable" in (a.id,b.id):raise ToolFailure("OSRM did not return this path","no_route")
        return original(a,b,mode,*args)
    agent.providers.routes=routes
    output=agent.plan_candidates(poi_ids=["unreachable","reachable"])
    assert output["route_ids"] and output["unreachable_combinations"][0]["poi_id"]=="unreachable"
    assert all(r.poi.id=="reachable" for r in agent.routes.values())
