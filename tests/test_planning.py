import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import pytest
from route_agent.fixtures.config import Settings, ROOT
from route_agent.fixtures import plan_route
from route_agent.fixtures.parser import parse_request, SG
from route_agent.fixtures.maps import load_context
from route_agent.fixtures.traffic import apply_traffic, match_speed_bands
from route_agent.fixtures.routing import generate_candidates, routing_graph
from route_agent.fixtures.verification import verify_route
from route_agent.fixtures.models import TaskState, TravelRequest
from route_agent.fixtures.tools import ToolRegistry, ToolFailure

DEPARTURE = {"departure_time":"2026-10-01T09:00:00+08:00"}
TEXT = "现在驾车从 City Hall 到 Orchard Road，30 分钟内到达。"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def deny(*args, **kwargs):
        raise AssertionError("Network forbidden in offline tests")
    monkeypatch.setattr(socket.socket, "connect", deny)


def test_parse_hard_and_soft_and_stop():
    req, questions, _ = parse_request("现在驾车从 City Hall 到 Orchard Road，30 分钟内到达，必须经过咖啡店，停留 5 分钟，希望避开拥堵，不能走高速。", DEPARTURE)
    assert not questions
    assert req.max_duration_s == 1800 and req.stop_duration_s == 300
    assert req.poi_required and req.prefer_avoid_congestion and req.avoid_highways


def test_clarification_round_trip():
    result = plan_route("从 City Hall 到 Orchard Road，必须经过咖啡店。", save=False)
    assert result.status == "needs_clarification"
    assert {"mode","departure_time","stop_duration_s"} <= result.questions.keys()
    result = plan_route(result.request.original_text, {**DEPARTURE,"mode":"driving","stop_duration_s":300}, save=False)
    assert result.status == "verified"


def test_unknown_traffic_cannot_pass_deadline():
    result = plan_route(TEXT, DEPARTURE, snapshot_id="demo-unknown", save=False)
    assert result.status == "unverified" and result.recommended is None
    assert result.unverified[0].traffic_coverage == 0
    assert any(c.constraint == "duration" and c.status == "unknown" for c in result.unverified[0].checks)


def test_replanning_expands_pois_without_relaxing_request():
    text = "现在驾车从 City Hall 到 Orchard Road，30 分钟内到达，必须经过咖啡店，停留 5 分钟。"
    result = plan_route(text, DEPARTURE, snapshot_id="demo-replan", save=False)
    assert result.status == "verified"
    assert result.state.attempts[0]["verified"] == 0
    assert result.metrics["replans"] == 1
    assert result.recommended.poi.id == "cafe-10"
    assert result.request.max_duration_s == 1800 and result.request.poi_required
    single = plan_route(text, DEPARTURE, snapshot_id="demo-replan", policy="no_replanning", save=False)
    assert single.status == "search_exhausted"


def test_stop_is_included_and_short_budget_fails():
    result = plan_route("现在驾车从 City Hall 到 Orchard Road，5 分钟内到达，必须经过咖啡店，停留 10 分钟。", DEPARTURE, save=False)
    assert result.status == "search_exhausted"
    assert all(r.total_s == r.driving_s + 600 for r in result.state.candidates)


def test_geometry_complete_and_direction_preserved():
    result = plan_route(TEXT, DEPARTURE, save=False)
    route = result.recommended
    assert route.geometry["geometry"]["coordinates"][0] == (route.origin.lon, route.origin.lat)
    assert route.geometry["geometry"]["coordinates"][-1] == (route.destination.lon, route.destination.lat)
    assert len(route.edges) == len(route.nodes)-1
    assert route.total_s > 0 and route.distance_m > 0
    context = load_context("replay","demo-clear",Settings())
    for u,v in zip(route.nodes,route.nodes[1:]):
        assert context.graph.has_edge(u,v)


def test_closed_edges_excluded():
    result = plan_route(TEXT, DEPARTURE, snapshot_id="demo-closure", save=False)
    assert result.status == "verified"
    assert "1:2:0" not in result.recommended.edges
    assert "1:4:0" not in result.recommended.edges


def test_highways_checked_across_all_legs():
    text = "现在驾车从 City Hall 到 Orchard Road，30 分钟内到达，必须经过咖啡店，停留 5 分钟，不能走高速。"
    result = plan_route(text, DEPARTURE, save=False)
    assert result.status == "verified" and not result.recommended.highways


def test_named_stop_not_replaced():
    text = '现在驾车从 City Hall 到 Orchard Road，12 分钟内到达，必须经过「Garden Cafe」，停留 5 分钟。'
    result = plan_route(text, DEPARTURE, save=False)
    assert result.status == "search_exhausted"
    assert all(r.poi.name == "Garden Cafe" for r in result.state.candidates)


def test_unsupported_constraints_must_be_explicitly_removed():
    text = TEXT + "下雨时要避雨，步行最多 100 米。"
    first = plan_route(text,DEPARTURE,save=False)
    assert first.status == "needs_clarification"
    assert "weather_required" in first.questions
    revised = plan_route(text,{**DEPARTURE,"weather_required":False,"max_walking_m":None},save=False)
    assert revised.status == "verified"


def test_stale_snapshot_does_not_claim_live_coverage():
    result = plan_route(TEXT,{"departure_time":"2026-10-02T09:00:00+08:00"},save=False)
    assert result.status == "unverified"
    assert not result.context["traffic"]["fresh"]


def test_tool_budget_cache_and_allowlist():
    state = TaskState(request=TravelRequest(original_text="test"))
    registry = ToolRegistry(state,max_calls=1)
    registry.register("test",lambda value:value)
    assert registry.call("test",value=1) == 1
    assert registry.call("test",value=1) == 1
    assert state.tools[-1].cached
    with pytest.raises(ToolFailure):
        registry.call("test",value=2)
    with pytest.raises(ToolFailure):
        registry.call("arbitrary_python")


def test_reverse_traffic_match_rejected():
    graph = load_context("replay","demo-clear",Settings()).graph
    u,v,k,edge = next(iter(graph.edges(keys=True,data=True)))
    a,b = edge["geometry"].coords[0],edge["geometry"].coords[-1]
    record = {"Location":f"{a[1]} {a[0]} {b[1]} {b[0]}","MinimumSpeed":10,"MaximumSpeed":20,"LinkID":"test"}
    matches,_ = match_speed_bands(graph,[record])
    assert f"{u}:{v}:{k}" in matches
    assert f"{v}:{u}:{k}" not in matches


def test_coarse_and_enumeration_agree_when_pool_fits():
    context = load_context("replay","demo-clear",Settings())
    req,_,_ = parse_request(TEXT+"必须经过咖啡店，停留 5 分钟。",DEPARTURE)
    meta = apply_traffic(context.graph, context.traffic, req.departure_time, replay=True)
    context.metadata["traffic"] = meta
    origin,dest = context.adapter.geocode(req.origin),context.adapter.geocode(req.destination)
    pois = context.adapter.search_pois("cafe")
    a,_ = generate_candidates(context.graph,origin,dest,pois,req,context.metadata,coarse_filter=True)
    b,_ = generate_candidates(context.graph,origin,dest,pois,req,context.metadata,coarse_filter=False)
    assert {r.id for r in a} == {r.id for r in b}


def test_speed_interval_boundary_is_unknown():
    context = load_context("replay","demo-clear",Settings())
    req,_,_ = parse_request(TEXT,DEPARTURE)
    for speed in context.traffic["edge_speeds"].values():
        speed["min_speed"] /= 10
    context.metadata["traffic"] = apply_traffic(context.graph,context.traffic,req.departure_time,replay=True)
    routes,_ = generate_candidates(context.graph,context.adapter.geocode(req.origin),context.adapter.geocode(req.destination),[],req,context.metadata)
    assert all(verify_route(r,req).feasibility == "unverified" for r in routes)


def test_saved_run_has_versions_hashes_and_valid_json(tmp_path):
    settings = replace(Settings(), root=tmp_path)
    (tmp_path/"data/snapshots").mkdir(parents=True)
    (tmp_path/"data/snapshots/demo-clear.json").write_bytes((ROOT/"data/snapshots/demo-clear.json").read_bytes())
    result = plan_route(TEXT,DEPARTURE,settings=settings)
    record = json.loads((tmp_path/"outputs"/result.run_id/"result.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path/"outputs"/result.run_id/"manifest.json").read_text(encoding="utf-8"))
    assert record["status"] == "verified" and manifest["packages"]["osmnx"]
    assert manifest["context"]["snapshot_sha256"]


def test_multiple_stops_requires_explicit_scope_confirmation():
    text = TEXT + "必须经过咖啡店和餐厅，停留 5 分钟。"
    result = plan_route(text,DEPARTURE,save=False)
    assert result.status == "needs_clarification" and "multiple_stops" in result.questions
    result = plan_route(text,{**DEPARTURE,"multiple_stops":False},save=False)
    assert result.status == "verified" and result.recommended.poi.category == "cafe"


def test_v4_traffic_and_reversed_osm_shape():
    from route_agent.fixtures.maps import normalize_graph
    from shapely.geometry import LineString
    graph = load_context("replay","demo-clear",Settings()).graph
    u,v,k,edge = next(iter(graph.edges(keys=True,data=True)))
    a,b = edge["geometry"].coords[0],edge["geometry"].coords[-1]
    edge["geometry"] = LineString([b,a])
    normalize_graph(graph)
    record = {"StartLat":a[1],"StartLon":a[0],"EndLat":b[1],"EndLon":b[0],
              "MinimumSpeed":10,"MaximumSpeed":20,"LinkID":"v4"}
    matched,_ = match_speed_bands(graph,[record])
    assert f"{u}:{v}:{k}" in matched and f"{v}:{u}:{k}" not in matched


def test_opening_time_interval_does_not_use_midpoint_as_proof():
    from route_agent.fixtures.verification import opening_check
    result = plan_route(TEXT+"必须经过咖啡店，停留 5 分钟。",DEPARTURE,save=False)
    route = result.recommended.model_copy(deep=True)
    route.poi.opening_hours = "08:00-09:20"
    route.evidence["lower_poi_arrival_s"] = 300
    route.evidence["upper_poi_arrival_s"] = 1200
    request = result.request.model_copy(update={"require_open":True})
    assert opening_check(route,request).status == "unknown"


def test_resolved_fixed_poi_id_survives_long_display_name():
    result = plan_route(TEXT+'必须经过「Central Cafe」，停留 5 分钟。',DEPARTURE,save=False)
    result.recommended.poi.name = "Central Cafe, Singapore"
    assert verify_route(result.recommended,result.request).feasibility == "verified"


def test_dev_and_test_requests_are_distinct_and_have_gold_fields():
    dev = json.loads((ROOT/"data/requests/dev.json").read_text(encoding="utf-8"))
    test = json.loads((ROOT/"data/requests/test.json").read_text(encoding="utf-8"))
    assert len(dev)==20 and len(test)==60
    assert not ({x["text"] for x in dev} & {x["text"] for x in test})
    for case in dev+test:
        req,_,_ = parse_request(case["text"],case["clarifications"])
        assert all(req.model_dump()[key] == value for key,value in case["gold_request"].items())


def test_single_unrelated_live_geocoder_hit_is_not_silently_accepted(tmp_path):
    from route_agent.fixtures.maps import LiveOSMAdapter
    graph = load_context("replay","demo-clear",Settings()).graph
    adapter = LiveOSMAdapter(graph,replace(Settings(),root=tmp_path))
    args = {"q":"City Hall MRT Station Singapore","countrycodes":"sg","format":"jsonv2","limit":5}
    adapter.cache.put("geocode",args,[{"display_name":"UOB, City Hall, Singapore",
        "lat":"1.2935","lon":"103.852","osm_type":"node","osm_id":123}])
    with pytest.raises(ToolFailure,match="尚不能确认"):
        adapter.geocode(args["q"])


def test_past_clock_deadline_is_not_silently_shifted_to_tomorrow():
    text = "现在驾车从 City Hall 到 Orchard Road，在 08:30 前到达。"
    request,questions,_ = parse_request(text,DEPARTURE)
    assert "arrival_deadline" in questions and request.arrival_deadline.day == 1
    request,questions,_ = parse_request(text,{**DEPARTURE,"arrival_deadline":"2026-10-02T08:30:00+08:00"})
    assert not questions and request.arrival_deadline.day == 2


def test_congestion_preference_changes_ranking_on_a_tradeoff():
    from route_agent.fixtures.ranking import rank_routes
    result = plan_route(TEXT,DEPARTURE,save=False)
    fast = result.recommended.model_copy(update={"id":"fast","total_s":600,"distance_m":2600,"congestion_fraction":.8},deep=True)
    clear = result.recommended.model_copy(update={"id":"clear","total_s":700,"distance_m":2600,"congestion_fraction":0},deep=True)
    assert rank_routes([fast,clear],result.request,600,2600)[0].id == "fast"
    assert rank_routes([fast,clear],result.request.model_copy(update={"prefer_avoid_congestion":True}),600,2600)[0].id == "clear"


def test_known_stop_conflict_fails_even_without_traffic():
    result = plan_route("现在驾车从 City Hall 到 Orchard Road，5 分钟内到达，必须经过咖啡店，停留 10 分钟。",
        DEPARTURE,snapshot_id="demo-unknown",save=False)
    assert result.status == "search_exhausted"
    assert all(any(c.constraint=="duration" and c.status=="fail" for c in route.checks) for route in result.state.candidates)


@pytest.mark.parametrize("case",json.loads((ROOT/"data/requests/dev.json").read_text(encoding="utf-8")),ids=lambda c:c["id"])
def test_development_cases(case):
    result = plan_route(case["text"],case["clarifications"],snapshot_id=case["snapshot_id"],save=False)
    assert result.status == case["expected_status"], result.model_dump()
