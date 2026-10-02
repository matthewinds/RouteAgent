"""OSRM protocol/guard tests. Mocked responses are not live-service acceptance."""
import socket
from dataclasses import replace
import httpx
import pytest
from route_agent.config import Settings
from route_agent.models import Place
from route_agent.providers import Providers
from route_agent.tools import Budget,ToolFailure

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(socket.socket,"connect",lambda *a:pytest.fail("OSRM tests must not call the network"))
    monkeypatch.delenv("ORS_API_KEY",raising=False)
    monkeypatch.setenv("OSRM_DRIVING_BASE_URL","https://osrm.test/car")
    monkeypatch.setenv("OSRM_WALKING_BASE_URL","https://osrm.test/foot")
    monkeypatch.setenv("OSRM_DRIVING_PROFILE","driving")
    monkeypatch.setenv("OSRM_WALKING_PROFILE","foot")
    settings=Settings(root=tmp_path)
    provider=Providers(settings,Budget(settings))
    a=Place(id="a",name="a",lon=103.85,lat=1.29,source="MOCK")
    b=Place(id="b",name="b",lon=103.86,lat=1.30,source="MOCK")
    # Skip actual time waits in protocol tests; pacing is tested separately.
    monkeypatch.setattr(Providers,"send_request",lambda self,client,method,url,**kwargs:client.request(
        method,url,**{k:v for k,v in kwargs.items() if k!="paced"}))
    return provider,a,b

def response(monkeypatch,payload,status=200):
    seen=[]
    def send(self,method,url,**kwargs):
        seen.append((method,url,kwargs))
        return httpx.Response(status,json=payload,request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",send)
    return seen

def payload(mode="driving"):
    coords=[[103.85,1.29],[103.855,1.295],[103.86,1.30]]
    return {"code":"Ok","routes":[{"distance":1500,"duration":700,
        "geometry":{"type":"LineString","coordinates":coords},"legs":[{"steps":[
            {"mode":mode,"name":"First Road","geometry":{"coordinates":coords[:2]},"distance":750,"duration":350},
            {"mode":mode,"name":"Second Road","geometry":{"coordinates":coords[1:]},"distance":750,"duration":350}]}]}]}

def test_real_provider_format_step_indices_and_no_ors_key(setup,monkeypatch):
    provider,a,b=setup
    seen=response(monkeypatch,payload())
    route=provider.routes(a,b,"driving")[0]
    assert [(s["start"],s["end"],s["name"]) for s in route.segments]==[(0,1,"First Road"),(1,2,"Second Road")]
    assert route.distance_m==1500 and route.provider_duration_s==700 and route.source=="OSRM"
    assert seen[0][0]=="GET" and seen[0][2]["json"] is None
    assert "Authorization" not in seen[0][2]["headers"]

@pytest.mark.parametrize("code,status,avoid,expected",[
    ("NoRoute",200,False,"no_route"),("NoSegment",400,False,"no_route"),
    ("InvalidValue",400,True,"unsupported_feature"),("InvalidOptions",200,True,"unsupported_feature"),
])
def test_service_failure_never_retries_without_hard_exclusion(setup,monkeypatch,code,status,avoid,expected):
    provider,a,b=setup
    seen=response(monkeypatch,{"code":code,"message":"untrusted provider message"},status)
    with pytest.raises(ToolFailure) as error:
        provider.routes(a,b,"driving",avoid)
    assert error.value.code==expected and len(seen)==1
    assert "untrusted" not in str(error.value)
    if avoid:
        assert seen[0][2]["params"]["exclude"]=="motorway"

@pytest.mark.parametrize("mode",["driving","ferry"])
def test_car_or_ferry_response_cannot_be_labelled_walking(setup,monkeypatch,mode):
    provider,a,b=setup
    response(monkeypatch,payload(mode))
    with pytest.raises(ToolFailure):
        provider.routes(a,b,"walking")

def test_same_car_endpoint_cannot_be_used_as_foot_graph(setup,monkeypatch):
    provider,a,b=setup
    provider.settings=replace(provider.settings,osrm_walking_base=provider.settings.osrm_driving_base)
    with pytest.raises(ToolFailure) as error:
        provider.routes(a,b,"walking")
    assert error.value.code=="invalid_configuration"

def test_motorway_marker_contradiction_is_rejected(setup,monkeypatch):
    provider,a,b=setup
    value=payload()
    value["routes"][0]["legs"][0]["steps"][0]["intersections"]=[{"classes":["motorway"]}]
    response(monkeypatch,value)
    with pytest.raises(ToolFailure,match="motorway"):
        provider.routes(a,b,"driving",True)

@pytest.mark.parametrize("failure",["empty","nonfinite","wrong_endpoint"])
def test_invalid_route_facts_are_rejected(setup,monkeypatch,failure):
    provider,a,b=setup
    value=payload()
    if failure=="empty":
        value["routes"]=[]
    elif failure=="nonfinite":
        value["routes"][0]["duration"]=-1
    else:
        value["routes"][0]["geometry"]["coordinates"][0]=[103.9,1.4]
    response(monkeypatch,value)
    with pytest.raises(ToolFailure) as error:
        provider.routes(a,b,"driving")
    assert error.value.code=="invalid_response"

def test_pacing_is_shared_across_provider_instances_and_failures(tmp_path,monkeypatch):
    import route_agent.providers as module
    clock=[100.0]
    monkeypatch.setattr(module.time,"monotonic",lambda:clock[0])
    monkeypatch.setattr(module.time,"sleep",lambda amount:clock.__setitem__(0,clock[0]+amount))
    monkeypatch.setattr(Providers,"_routing_last_request",0.0)
    settings=Settings(root=tmp_path)
    first,second=Providers(settings,Budget(settings)),Providers(settings,Budget(settings))
    stamps=[]
    class Client:
        def request(self,*a,**kw):
            stamps.append(clock[0])
            if len(stamps)==2:
                raise httpx.ConnectError("MOCK")
            return "ok"
    assert first.send_request(Client(),"GET","https://osrm.test",paced=True)=="ok"
    with pytest.raises(httpx.ConnectError):
        second.send_request(Client(),"GET","https://osrm.test",paced=True)
    first.send_request(Client(),"GET","https://osrm.test",paced=True)
    assert all(b-a>=1.05-1e-8 for a,b in zip(stamps,stamps[1:]))
    assert first.budget.http_calls==2 and second.budget.http_calls==1
