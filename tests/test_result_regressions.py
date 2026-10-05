"""Regressions from the driving/refuelling result; no live API calls."""
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
