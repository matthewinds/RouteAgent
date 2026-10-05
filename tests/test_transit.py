"""OneMap documented response protocol, mocked; not live service acceptance."""
import copy
import socket
from datetime import datetime,timedelta,timezone
import pytest
from route_agent.config import Settings
from route_agent.models import Evidence,Place,TravelRequest
from route_agent.providers import Providers,now_utc
from route_agent.tools import Budget,ToolFailure
from route_agent.transit import decode_polyline
from test_online import make_agent


@pytest.fixture
def transit_setup(tmp_path,monkeypatch):
    monkeypatch.setattr(socket.socket,"connect",lambda *a,**k:pytest.fail("No real network in protocol tests"))
    monkeypatch.setenv("ONEMAP_TOKEN","test-only-token")
    settings=Settings(root=tmp_path); provider=Providers(settings,Budget(settings))
    a=Place(id="a",name="Origin",lat=1.29,lon=103.85,source="MOCK")
    b=Place(id="b",name="Destination",lat=1.30,lon=103.86,source="MOCK")
    start=datetime(2026,10,5,2,0,tzinfo=timezone.utc)
    request=TravelRequest(original_text="公交出行",origin=a.name,destination=b.name,mode="transit",departure_time=start)
    def millis(seconds):return int((start+timedelta(seconds=seconds)).timestamp()*1000)
    payload={"plan":{"itineraries":[{"duration":840,"startTime":millis(60),"endTime":millis(900),"fare":"2.50",
        "legs":[{"mode":"WALK","startTime":millis(60),"endTime":millis(180),"distance":150,
            "from":{"name":"Origin","lat":1.29,"lon":103.85},"to":{"name":"Bus Stop","lat":1.291,"lon":103.851},
            "legGeometry":{"points":"o}zFoezxRgEgE"}},
            {"mode":"BUS","routeShortName":"123","startTime":millis(300),"endTime":millis(900),"distance":1500,
            "from":{"name":"Bus Stop","lat":1.291,"lon":103.851},"to":{"name":"Destination","lat":1.30,"lon":103.86},
            "legGeometry":{"points":"wc{FwkzxRgw@gw@"}}]}]}}
    provider.evidence["onemap"]=Evidence(id="onemap",source="OneMap transit",retrieved_at=now_utc(),
        valid_until=now_utc()+timedelta(minutes=1),tool_call_id="mock",response_sha256="mock",summary="MOCK")
    seen=[]
    def response(*args,**kwargs):
        seen.append(kwargs);return payload,"onemap"
    monkeypatch.setattr(provider,"request",response)
    return provider,a,b,request,payload,seen


def test_public_transport_fare_waiting_and_service_geometry(transit_setup):
    provider,a,b,request,payload,seen=transit_setup
    route=provider.transit_routes(a,b,request)[0]
    assert route.mode=="transit" and route.fare_sgd==2.5 and route.fare_evidence_ids==["onemap"]
    assert route.total_s==900 and route.provider_total_s==900  # Includes 60s initial and 120s transfer wait.
    assert [leg.mode for leg in route.legs]==["walking","bus"]
    assert route.legs[1].service_name=="123" and route.walking_m==150
    assert seen[0]["params"]["date"]=="10-05-2026" and seen[0]["params"]["time"]=="10:00:00"
    assert seen[0]["headers"]["Authorization"]=="test-only-token"
    assert "token" not in seen[0]["params"]


def test_missing_fare_remains_unknown(transit_setup):
    provider,a,b,request,payload,seen=transit_setup
    payload["plan"]["itineraries"][0].pop("fare")
    route=provider.transit_routes(a,b,request)[0]
    assert route.fare_sgd is None and not route.fare_evidence_ids


def test_default_departure_rounds_up_to_onemap_minute_resolution(transit_setup):
    provider,a,b,request,payload,seen=transit_setup
    itinerary=payload["plan"]["itineraries"][0]
    itinerary["startTime"]+=60000
    itinerary["endTime"]+=60000
    for leg in itinerary["legs"]:
        leg["startTime"]+=60000
        leg["endTime"]+=60000
    request.departure_time=request.departure_time.replace(microsecond=600000)
    route=provider.transit_routes(a,b,request)[0]
    assert route.total_s==pytest.approx(959.4)
    assert seen[0]["params"]["time"]=="10:01:00"


@pytest.mark.parametrize("proof",[{}, {"evidence_ids":None}])
def test_finish_tool_uses_tracked_evidence_when_omitted_or_null(transit_setup,tmp_path,proof):
    import json
    provider,a,b,request,payload,seen=transit_setup
    route=provider.transit_routes(a,b,request)[0]
    agent=make_agent(tmp_path,mode="transit")
    agent.request.departure_time=request.departure_time
    agent.providers.evidence.update(provider.evidence)
    agent.providers.transit_routes=lambda *args:[route]
    agent.plan_candidates()
    output=agent.execute("finish_plan",json.dumps({"selected_id":route.id,**proof}),"finish-proof")
    assert output["status"]=="verified" and "onemap" in agent.result.context["model_evidence_ids"]


@pytest.mark.parametrize("bad_fare",["NaN","-2","Infinity","not-a-price"])
def test_invalid_fare_is_not_published(transit_setup,bad_fare):
    provider,a,b,request,payload,seen=transit_setup
    payload["plan"]["itineraries"][0]["fare"]=bad_fare
    with pytest.raises(ToolFailure) as error:provider.transit_routes(a,b,request)
    assert error.value.code=="invalid_response"


def test_missing_geometry_does_not_create_straight_transit_connector(transit_setup):
    provider,a,b,request,payload,seen=transit_setup
    payload["plan"]["itineraries"][0]["legs"][1].pop("legGeometry")
    with pytest.raises(ToolFailure):provider.transit_routes(a,b,request)


def test_transit_can_complete_grounded_plan_and_honour_constraints(transit_setup,tmp_path):
    provider,a,b,request,payload,seen=transit_setup
    route=provider.transit_routes(a,b,request)[0]
    agent=make_agent(tmp_path,mode="transit",prefer_low_cost=True,max_duration_s=1000,max_walking_m=200)
    agent.request.departure_time=request.departure_time
    agent.providers.evidence.update(provider.evidence)
    agent.providers.transit_routes=lambda *args:[route]
    agent.plan_candidates()
    agent.finish_plan(route.id,reason_codes=["cost","time"])
    assert agent.result.status=="verified"
    assert all(c.status=="pass" for c in agent.result.recommended.checks)


def test_transit_does_not_silently_remove_required_stop(tmp_path):
    agent=make_agent(tmp_path,mode="transit",poi_required=True,poi_name="Hospital")
    with pytest.raises(ToolFailure) as error:agent.plan_candidates()
    assert error.value.code=="missing_information" and not agent.routes


def test_transit_token_is_optional_but_missing_is_an_explicit_failure(transit_setup,monkeypatch):
    provider,a,b,request,payload,seen=transit_setup
    monkeypatch.delenv("ONEMAP_TOKEN")
    with pytest.raises(ToolFailure) as error:provider.transit_routes(a,b,request)
    assert error.value.code=="missing_credential" and not seen
