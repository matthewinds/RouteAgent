"""Protocol/security/route-binding regressions. Fixtures are not live acceptance."""
import io
import json
import socket
import zipfile
from datetime import timedelta
import pytest
from google.transit import gtfs_realtime_pb2
from route_agent.config import Settings
from route_agent.providers import Providers,now_utc
from route_agent.models import Evidence,Place,RouteLeg,RouteCandidate,TravelRequest
from route_agent.tools import Budget,ToolFailure
from route_agent.lta_data import DATASETS,query,parse_file,download,clean
from route_agent.lta_verification import operational_checks
from route_agent.coordinator import tool_schemas
from test_online import make_agent

@pytest.fixture
def provider(tmp_path,monkeypatch):
    monkeypatch.setenv("LTA_API_KEY","fixture-lta-key")
    monkeypatch.setattr(socket.socket,"connect",lambda *a,**k:pytest.fail("No live requests in LTA regression tests"))
    settings=Settings(root=tmp_path);p=Providers(settings,Budget(settings))
    p.evidence["ev"]=Evidence(id="ev",source="LTA mock",retrieved_at=now_utc(),valid_until=now_utc()+timedelta(minutes=5),tool_call_id="test",response_sha256="mock",summary="MOCK")
    return p

def test_catalogue_has_official_paths_and_schema():
    assert len(DATASETS)==33
    schema=next(x["function"] for x in tool_schemas() if x["function"]["name"]=="get_lta_data")
    assert set(schema["parameters"]["properties"]["dataset"]["enum"])==set(DATASETS)
    assert DATASETS["bus_arrival"]["endpoint"]=="v3/BusArrival"
    assert DATASETS["faulty_lights"]["endpoint"]=="v2/FaultyTrafficLights"
    assert DATASETS["gtfs_updates"]["endpoint"]=="GTFSRealtimeTrainTripUpdates"

@pytest.mark.parametrize("dataset,parameters",[("bus_arrival",{}),("bus_arrival",{"BusStopCode":"123"}),("ev_charging",{"PostalCode":"../secret"}),
    ("station_crowd",{"TrainLine":"fake"}),("bus_stops",{"url":"https://example.com"}),("geospatial",{"ID":"FakeLayer"})])
def test_invalid_parameters_fail_before_request(provider,monkeypatch,dataset,parameters):
    monkeypatch.setattr(provider,"request",lambda *a,**k:pytest.fail("Request must not run"))
    with pytest.raises(ToolFailure) as error:query(provider,dataset,parameters)
    assert error.value.code=="invalid_arguments"

def test_page_limits_and_local_filters_preserve_partial_evidence(provider,monkeypatch):
    seen=[]
    def response(*a,**kw):
        seen.append(kw)
        return {"value":[{"BusStopCode":str(i+kw["params"]["$skip"]),"Description":"station"} for i in range(500)]},"ev"
    monkeypatch.setattr(provider,"request",response)
    result=query(provider,"bus_stops",max_pages=2,filters={"BusStopCode":"700"})
    assert [x["params"]["$skip"] for x in seen]==[0,500]
    assert result["collection"]["next_skip"]==1000 and not result["collection"]["complete"]
    assert result["matched_count"]==1 and result["records"][0]["BusStopCode"]=="700"
    assert len(provider.lta_queries[0]["records"])==1000
    assert "AccountKey" not in json.dumps(result)

def test_partial_failure_and_repeated_page_are_not_complete(provider,monkeypatch):
    data={"value":[{"id":i} for i in range(500)]}
    monkeypatch.setattr(provider,"request",lambda *a,**k:(data,"ev"))
    result=query(provider,"bus_stops",max_pages=2)
    assert not result["collection"]["complete"] and result["collection"]["reason"]=="repeated_page"

def test_bus_alert_and_ev_actual_response_shapes(provider,monkeypatch):
    for dataset,payload,params in [("bus_arrival",{"Services":[{"ServiceNo":"123"}]},{"BusStopCode":"12345"}),
        ("train_alerts",{"value":{"Status":1,"AffectedSegments":[]}},{ }),
        ("ev_charging",{"value":{"evLocationsData":[{"name":"Actual source station","chargingPoints":[] }]}},{"PostalCode":"123456"})]:
        monkeypatch.setattr(provider,"request",lambda *a,payload=payload,**k:(payload,"ev"))
        result=query(provider,dataset,params)
        assert result["collection"]["complete"] and result["record_count"]==1

def test_signed_file_urls_never_reach_results_cache_or_headers(provider,monkeypatch):
    import route_agent.lta_data as module
    signed="https://dmprod-datasets.s3.ap-southeast-1.amazonaws.com/feed.pb?X-Amz-Security-Token=private-token"
    monkeypatch.setattr(provider,"request",lambda *a,**kw:({"value":[{"link":signed,"timestamp":"2026-10-05"}]},"ev"))
    feed=gtfs_realtime_pb2.FeedMessage();feed.header.gtfs_realtime_version="2.0";feed.header.timestamp=int(now_utc().timestamp())
    monkeypatch.setattr(module,"download",lambda p,url:feed.SerializeToString())
    result=query(provider,"gtfs_alerts")
    assert result["file"]["read"] and result["file"]["record_count"]==0
    assert "private-token" not in json.dumps(result)
    assert all("private-token" not in p.read_text() for p in provider.cache.directory.glob("*.json"))
    assert provider.evidence["ev"].valid_until
    cached=query(provider,"gtfs_alerts")
    assert cached["file"]["observation_fresh"] and "private-token" not in json.dumps(cached)

def test_unread_manifest_does_not_claim_read_file(provider,monkeypatch):
    monkeypatch.setattr(provider,"request",lambda *a,**k:({"value":[{"Link":"https://dmgeospatial.s3.amazonaws.com/file.zip?secret=token"}]},"ev"))
    result=query(provider,"geospatial",{"ID":"TrainStation"},read_file=False)
    assert result["file"]["read"] is False and "token" not in json.dumps(result)

@pytest.mark.parametrize("url",["http://dmprod-datasets.s3.amazonaws.com/f","https://example.com/f","https://unrelated.s3.amazonaws.com/f","https://dmprod-datasets.s3.amazonaws.com:1234/f"])
def test_download_only_official_manifest_hosts(provider,url):
    with pytest.raises(ToolFailure) as error:download(provider,url)
    assert error.value.code=="invalid_response" and provider.budget.http_calls==0

def test_gtfs_csv_and_protobuf_decode_real_records_without_generated_geometry():
    content=io.BytesIO()
    with zipfile.ZipFile(content,"w") as z:z.writestr("stops.txt","stop_id,stop_name\nDT1,Station One\nDT2,Station Two\n")
    parsed=parse_file(content.getvalue(),"feed.zip",1)
    assert parsed["tables"]["stops.txt"]["record_count"]==2 and parsed["truncated"]
    feed=gtfs_realtime_pb2.FeedMessage();feed.header.gtfs_realtime_version="2.0"
    entity=feed.entity.add();entity.id="real-feed-id";entity.alert.effect=gtfs_realtime_pb2.Alert.NO_SERVICE
    assert parse_file(feed.SerializeToString(),"feed.pb",10)["records"][0]["alert"]["effect"]=="NO_SERVICE"
    filtered=parse_file(content.getvalue(),"feed.zip",1,"stops.txt",{"stop_id":"DT2"})
    assert filtered["records"]==[{"stop_id":"DT2","stop_name":"Station Two"}] and not filtered["truncated"]

def itinerary(mode="rail",code="DT1"):
    start=now_utc();a=Place(id="a",name="Station",lat=1.29,lon=103.85,source="OneMap transit",stop_code=code)
    b=a.model_copy(update={"id":"b","stop_code":"DT2","lat":1.30,"lon":103.86})
    leg=RouteLeg(id="leg",mode=mode,origin=a,destination=b,service_name="DT" if mode=="rail" else "123",
        geometry={"type":"LineString","coordinates":[[103.85,1.29],[103.86,1.30]]},distance_m=1000,provider_duration_s=600,
        departure_time=start+timedelta(seconds=60),arrival_time=start+timedelta(seconds=660))
    route=RouteCandidate(id="route",mode="transit",origin=a,destination=b,legs=[leg],geometry=leg.geometry,distance_m=1000,provider_total_s=660,total_s=660)
    return route,TravelRequest(original_text="test",departure_time=start,mode="transit")

def observation(dataset,records,parameters=None):
    return {"dataset":dataset,"records":records,"parameters":parameters or {},"evidence_ids":["ev"],"temporal_basis":"current"}

@pytest.mark.parametrize("stations,status",[("DT1,DT2","fail"),("DT3,DT4","unknown")])
def test_train_disruption_rejects_or_requires_station_sequence(provider,stations,status):
    route,request=itinerary();queries=[observation("train_alerts",[{"Status":2,"AffectedSegments":[{"Line":"DTL","Stations":stations}]}])]
    checks,_,_=operational_checks(route,request,queries,provider.evidence)
    assert checks[0].status==status and checks[0].hard

def test_missing_expired_or_future_observation_does_not_certify_rail(provider):
    route,request=itinerary()
    checks,required,_=operational_checks(route,request,[],provider.evidence)
    assert checks[0].status=="unknown" and required==[{"dataset":"train_alerts"}]
    provider.evidence["ev"].valid_until=now_utc()-timedelta(seconds=1)
    checks,_,_=operational_checks(route,request,[observation("train_alerts",[{"Status":1}])],provider.evidence)
    assert checks[0].status=="unknown"
    request.departure_time+=timedelta(days=1)
    checks,required,_=operational_checks(route,request,[],provider.evidence)
    assert checks[0].constraint=="lta_current_reference" and not required

def test_disruption_with_missing_segments_is_unknown_not_normal(provider):
    route,request=itinerary()
    checks,_,_=operational_checks(route,request,[observation("train_alerts",[{"Status":2,"AffectedSegments":[]}])],provider.evidence)
    assert checks[0].status=="unknown" and checks[0].hard

def test_bus_arrival_binding_does_not_shift_onward_schedule(provider):
    route,request=itinerary("bus","12345");initial=route.model_dump()
    record={"ServiceNo":"123","NextBus":{"EstimatedArrival":route.legs[0].departure_time.isoformat(),"Load":"SEA"}}
    queries=[observation("bus_arrival",[record],{"BusStopCode":"12345","ServiceNo":"123"})]
    checks,required,_=operational_checks(route,request,queries,provider.evidence)
    assert checks[0].status=="pass" and not required and route.model_dump()==initial
    queries[0]["parameters"]["BusStopCode"]="54321"
    checks,required,_=operational_checks(route,request,queries,provider.evidence)
    assert checks[0].status=="unknown" and required[0]["parameters"]["BusStopCode"]=="12345"

def test_forecast_is_bound_to_station_date_and_boarding_interval(provider):
    route,request=itinerary()
    record={"Date":request.departure_time.isoformat(),"Stations":[{"Station":"DT1","Interval":[{"Start":(request.departure_time-timedelta(minutes=1)).isoformat(),"CrowdLevel":"h"}]}]}
    queries=[observation("station_crowd_forecast",[record])]
    _,_,advice=operational_checks(route,request,queries,provider.evidence)
    assert advice[0]["record"]["CrowdLevel"]=="h"
    record["Stations"][0]["Interval"][0]["Start"]=(request.departure_time-timedelta(days=1)).isoformat()
    assert not operational_checks(route,request,queries,provider.evidence)[2]

def test_agent_rejects_invented_nearby_coordinates(tmp_path):
    agent=make_agent(tmp_path)
    with pytest.raises(ToolFailure) as error:agent.get_lta_data("bicycle_parking",{"Lat":1.42,"Long":103.67})
    assert error.value.code=="invalid_evidence"

def test_publication_requires_route_specific_lta_attempt_and_keeps_failure_visible(tmp_path):
    agent=make_agent(tmp_path,mode="transit")
    route,request=itinerary("bus","12345")
    route.evidence_ids=["osrm"];route.legs[0].evidence_ids=["osrm"]
    agent.request.departure_time=request.departure_time
    agent.routes[route.id]=route;agent.result.state.attempts=[{}]
    def unavailable(**kwargs):raise ToolFailure("LTA 暂时不可用","provider_error")
    agent.providers.query_lta=unavailable
    with pytest.raises(ToolFailure,match="check_lta_routes"):agent.finish_plan(route.id)
    output=agent.check_lta_routes([route.id])
    assert output["routes"][route.id]["required_queries"][0]["parameters"]["BusStopCode"]=="12345"
    with pytest.raises(ToolFailure):agent.get_lta_data("bus_arrival",{"BusStopCode":"54321","ServiceNo":"123"})
    with pytest.raises(ToolFailure,match="尚未尝试"):agent.finish_plan(route.id)
    with pytest.raises(ToolFailure):agent.get_lta_data("bus_arrival",{"BusStopCode":"12345","ServiceNo":"123"})
    agent.finish_plan(route.id)
    assert agent.result.status=="unverified" and agent.result.recommended
    assert any("公交到站" in reason for reason in agent.result.context["unmet_preferences"])
