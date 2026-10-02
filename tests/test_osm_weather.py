"""OSM and Open-Meteo protocol tests; mocks are not real connectivity evidence."""
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from test_online import make_agent
from route_agent.config import Settings
from route_agent.providers import Providers, now_utc
from route_agent.tools import Budget, ToolFailure
from route_agent.models import Place
from route_agent.weather import weather_context

@pytest.fixture
def providers(tmp_path):
    return Providers(Settings(root=tmp_path),Budget(Settings(root=tmp_path)))

@pytest.mark.parametrize("category",["hospital","restaurant","cafe"])
def test_osm_category_queries_with_stable_ids(providers,monkeypatch,category):
    seen=[]
    def response(self,method,url,**kw):
        seen.append(kw)
        return httpx.Response(200,json={"elements":[{"type":"way","id":42,"center":{"lat":1.29,"lon":103.85},
            "tags":{"name":"Real tagged POI","amenity":category,"opening_hours":"24/7"}}]},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    p=Place(id="center",name="Center",lat=1.29,lon=103.85,source="User coordinate")
    result=providers.pois(p,category)
    assert f'["amenity"="{category}"]' in seen[0]["params"]["data"]
    assert result[0].id=="osm:way:42" and result[0].category==category
    assert result[0].opening_hours=="24/7" and result[0].evidence_ids
    assert "Authorization" not in seen[0].get("headers",{})

def test_named_unnamed_osm_poi_does_not_crash(providers,monkeypatch):
    monkeypatch.setattr(providers,"request",lambda *a,**k:({"elements":[{"type":"node","id":3,"lat":1.29,"lon":103.85,"tags":{"amenity":"restaurant"}}]},"ev"))
    p=Place(id="center",name="Center",lat=1.29,lon=103.85,source="User coordinate")
    assert providers.pois(p,None,name="OSM restaurant")[0].category=="restaurant"

def test_osm_roads_preserve_unknown_tags(providers,monkeypatch):
    seen=[]
    def response(self,method,url,**kw):
        seen.append(kw)
        return httpx.Response(200,json={"elements":[{"type":"way","id":88,"tags":{"name":"Road A","highway":"residential","oneway":"yes"},
            "geometry":[{"lat":1.29,"lon":103.85},{"lat":1.291,"lon":103.851}]}]},request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    p=Place(id="center",name="Center",lat=1.29,lon=103.85,source="User coordinate")
    result=providers.roads(p)
    assert '["highway"]' in seen[0]["params"]["data"] and 'out geom tags' in seen[0]["params"]["data"]
    road=result["roads"][0]
    assert road["id"]=="osm:way:88" and road["tags"]["oneway"]=="yes"
    assert road["tags"]["maxspeed"] is None and road["tags"]["access"] is None

def meteo_payload(probability=20):
    end=now_utc().replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
    return {"latitude":1.25,"longitude":103.75,"hourly_units":{"precipitation_probability":"%","precipitation":"mm"},
        "hourly":{"time":[int((end+timedelta(hours=i)).timestamp()) for i in range(4)],
            "precipitation_probability":[probability]*4,"precipitation":[0.1]*4,"weather_code":[3]*4}}

def test_open_meteo_no_key_and_preceding_hour(providers,monkeypatch):
    seen=[]
    def response(self,method,url,**kw):
        seen.append((url,kw)); return httpx.Response(200,json=meteo_payload(),request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    result=providers.weather([[103.85,1.29]])
    assert seen[0][0].endswith("/v1/forecast") and not seen[0][1].get("headers")
    assert seen[0][1]["params"]["timeformat"]=="unixtime"
    sample=result["points"][0]
    assert sample["requested_coordinate"]==[103.85,1.29] and sample["grid_coordinate"]==[103.75,1.25]
    hour=sample["hours"][0]
    assert datetime.fromisoformat(hour["end"])-datetime.fromisoformat(hour["start"])==timedelta(hours=1)
    assert hour["precipitation_probability"]==20

def test_open_meteo_multiple_coordinates_and_bad_units(providers,monkeypatch):
    def response(self,method,url,**kw):
        return httpx.Response(200,json=[meteo_payload(),meteo_payload()],request=httpx.Request(method,url))
    monkeypatch.setattr(httpx.Client,"request",response)
    assert len(providers.weather([[103.85,1.29],[103.86,1.30]])["points"])==2
    bad=meteo_payload(); bad["hourly_units"]["precipitation"]="inch"
    monkeypatch.setattr(providers,"request",lambda *a,**k:(bad,"bad"))
    with pytest.raises(ToolFailure,match="单位"):
        providers.weather([[103.85,1.29]])

def test_weather_nulls_expiry_missing_samples(tmp_path):
    agent=make_agent(tmp_path); agent.plan_candidates(); route=next(iter(agent.routes.values()))
    payload=agent.providers.weather()
    context=weather_context(route,agent.request,payload,agent.providers.evidence)
    assert context.status=="available" and context.rain_fraction==.2
    assert context.samples and "不是整程" in context.risk_basis
    for point in payload["points"]:
        for hour in point["hours"]:
            hour["precipitation_probability"]=None
    assert weather_context(route,agent.request,payload,agent.providers.evidence).status=="unknown"
    payload=agent.providers.weather([[103.7,1.20]])
    assert weather_context(route,agent.request,payload,agent.providers.evidence).status=="unknown"
    agent.providers.evidence["meteo"].valid_until=now_utc()-timedelta(seconds=1)
    assert weather_context(route,agent.request,agent.providers.weather(),agent.providers.evidence).status=="unknown"

def test_weather_tool_uses_known_geometry_and_road_tool_records(tmp_path):
    agent=make_agent(tmp_path); agent.plan_candidates()
    result=agent.execute("get_weather","{}","meteo-query")
    assert result["provider"]=="open_meteo" and result["sample_count"]<=8
    assert agent.weather_for(next(iter(agent.routes.values()))).status=="available"
    agent.providers.roads=lambda *a,**k:{"roads":[{"id":"osm:way:123","name":"Road","tags":{},"geometry":{},"evidence_ids":["geo"]}],"evidence_ids":["geo"]}
    agent.get_roads()
    assert "osm:way:123" in agent.result.context["osm_roads"]
    invalid=agent.execute("get_weather",'{"route_ids":["invented"]}',"invalid-weather")
    assert invalid["error"]=="unknown_id"

def test_hour_boundary_excludes_next_hour_and_final_check_expires(tmp_path):
    agent=make_agent(tmp_path); agent.plan_candidates(); route=next(iter(agent.routes.values()))
    start=now_utc().replace(minute=0,second=0,microsecond=0)
    agent.request.departure_time=start
    route.total_s=3600
    payload=agent.providers.weather()
    for point in payload["points"]:
        point["hours"][1]["precipitation_probability"]=100
    context=weather_context(route,agent.request,payload,agent.providers.evidence)
    assert context.rain_fraction==.2
    agent.weather=payload; route.weather=context
    agent.providers.evidence["meteo"].valid_until=now_utc()-timedelta(seconds=1)
    agent.validate_routes()
    assert route.weather.status=="unknown"

def test_nea_supplement_does_not_replace_primary(tmp_path):
    agent=make_agent(tmp_path); agent.plan_candidates(); agent.get_weather()
    agent.providers.nea_weather=lambda:{"data":{"items":[]},"evidence_ids":["geo"]}
    result=agent.get_weather(provider="nea")
    assert result["provider"]=="nea" and agent.weather["provider"]=="open_meteo"
    assert agent.result.context["weather"]["provider"]=="open_meteo"
    assert agent.result.context["nea_weather_supplement"]["evidence_ids"]==["geo"]

def test_overpass_partial_runtime_failure_is_not_empty_success(providers,monkeypatch):
    monkeypatch.setattr(providers,"request",lambda *a,**k:({"elements":[],"remark":"runtime error: timed out"},"ev"))
    with pytest.raises(ToolFailure,match="未完整"):
        providers.overpass("query","OSM roads")
