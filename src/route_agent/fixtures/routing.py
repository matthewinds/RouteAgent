"""Routing and bounded waypoint combinations; the LLM never invents a path."""
import hashlib
import itertools
import math
import networkx as nx
from .models import RouteCandidate
from .maps import distance_m


def routing_graph(graph, request, strategy):
    compact = nx.DiGraph()
    compact.add_nodes_from(graph.nodes(data=True))
    for u, v, k, edge in graph.edges(keys=True, data=True):
        if edge.get("closed") or (request.avoid_highways and edge["highway_route"]):
            continue
        if strategy == "shortest":
            cost = edge["length"]
        elif strategy == "base_fastest":
            cost = edge["base_time_s"]
        else:
            cost = edge["travel_time"]
            if strategy == "less_congestion" and edge["congested"]:
                cost *= 1.75
        if request.prefer_avoid_highways and edge["highway_route"]:
            cost *= 1.5
        if not compact.has_edge(u, v) or cost < compact[u][v]["cost"]:
            compact.add_edge(u, v, cost=cost, original_key=k)
    return compact


def shortest_paths(compact, origin, destination, k):
    if origin == destination:
        return [[origin]]
    try:
        return list(itertools.islice(nx.shortest_simple_paths(compact, origin, destination, weight="cost"), k))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []


def build_candidate(graph, compact, legs, origin, destination, poi, request, strategy, metadata):
    nodes = list(legs[0])
    for leg in legs[1:]:
        nodes.extend(leg[1:])
    edge_ids, coordinates = [], []
    length = duration = known_length = congested_length = lower = 0.0
    upper = 0.0
    all_upper_known = True
    highway, closed = False, False
    first_leg_s = 0
    first_leg_lower, first_leg_upper = 0.0, None
    for index, leg in enumerate(legs):
        leg_duration = 0
        for u, v in zip(leg, leg[1:]):
            edge = graph[u][v][compact[u][v]["original_key"]]
            edge_ids.append(edge["edge_id"])
            shape = list(edge["geometry"].coords)
            start = (graph.nodes[u]["x"], graph.nodes[u]["y"])
            if math.dist(start, shape[-1]) < math.dist(start, shape[0]):
                shape.reverse()
            coordinates.extend(shape if not coordinates else shape[1:])
            length += edge["length"]
            duration += edge["travel_time"]
            leg_duration += edge["travel_time"]
            lower += edge.get("time_lower_s", edge["base_time_s"])
            high = edge.get("time_upper_s")
            if high is None:
                all_upper_known = False
            else:
                upper += high
            if edge["traffic_known"]:
                known_length += edge["length"]
                if edge["congested"]:
                    congested_length += edge["length"]
            highway |= edge["highway_route"]
            closed |= edge["closed"]
        if index == 0:
            first_leg_s = leg_duration
            first_leg_lower, first_leg_upper = lower, upper if all_upper_known else None
    if not coordinates:
        coordinates = [[origin.lon, origin.lat], [destination.lon, destination.lat]]
    stop = (request.stop_duration_s or 0) if poi else 0
    identity = hashlib.sha256(("|".join(edge_ids) + (poi.id if poi else "direct")).encode()).hexdigest()[:14]
    coverage = known_length/length if length else 1
    return RouteCandidate(id=identity, strategy=strategy, nodes=nodes, edges=edge_ids,
        geometry={"type": "Feature", "properties": {"id": identity},
                  "geometry": {"type": "LineString", "coordinates": coordinates}},
        origin=origin, destination=destination, poi=poi, distance_m=length,
        driving_s=duration, stop_s=stop, total_s=duration+stop,
        poi_arrival_s=first_leg_s if poi else None, traffic_coverage=coverage,
        congestion_fraction=congested_length/known_length if known_length else 0,
        unknown_fraction=1-coverage, highways=highway, closed=closed,
        evidence={"traffic": metadata.get("traffic", {}), "graph_source": metadata.get("graph_source"),
                  "synthetic": metadata.get("synthetic", False), "lower_total_s": lower+stop,
                  "upper_total_s": upper+stop if all_upper_known else None,
                  "lower_poi_arrival_s": first_leg_lower, "upper_poi_arrival_s": first_leg_upper,
                  "poi_access": "nearest drive node; parking/walking not modeled",
                  "snapshot_id": metadata.get("snapshot_id")})


def generate_candidates(graph, origin, destination, pois, request, metadata, *, poi_limit=10,
                        k=3, coarse_filter=True, strategies=None):
    strategies = strategies or ["fastest", "shortest"]
    if request.prefer_avoid_congestion:
        strategies = [*strategies, "less_congestion"]
    ordered = sorted(pois, key=lambda p: distance_m((origin.lat, origin.lon), (p.lat, p.lon)) +
                     distance_m((p.lat, p.lon), (destination.lat, destination.lon)))
    has_poi_request = bool(request.poi_category or request.poi_name)
    selected = ordered[:poi_limit] if coarse_filter else ordered[:30]
    candidates, duplicates, pruned = {}, 0, 0
    # Soft stops are included as options and direct travel remains a valid option.
    stops = selected if has_poi_request else [None]
    if has_poi_request and not request.poi_required:
        stops = [None, *stops]
    for strategy in strategies:
        compact = routing_graph(graph, request, strategy)
        for poi in stops:
            if coarse_filter and poi and poi.opening_hours == "closed" and request.require_open:
                pruned += 1
                continue
            if poi:
                a = shortest_paths(compact, origin.node, poi.node, k)
                b = shortest_paths(compact, poi.node, destination.node, k)
                pairs = itertools.product(a, b)
            else:
                pairs = ([path] for path in shortest_paths(compact, origin.node, destination.node, k))
            for legs in pairs:
                candidate = build_candidate(graph, compact, list(legs), origin, destination, poi, request, strategy, metadata)
                if candidate.id in candidates:
                    duplicates += 1
                else:
                    candidates[candidate.id] = candidate
    return list(candidates.values()), {"poi_pool": len(pois), "selected_pois": len(selected),
        "truncated": max(0, len(ordered)-len(selected)), "safe_pruned": pruned,
        "duplicates": duplicates, "candidate_count": len(candidates)}
