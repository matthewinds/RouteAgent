"""Traffic evidence: directed geometry matching, coverage, and bounded estimates."""
import os
from datetime import datetime, timezone
import httpx
from shapely.geometry import LineString, Point
from shapely.ops import transform
from shapely.strtree import STRtree
from pyproj import Transformer
from .tools import ToolFailure


def fetch_lta(settings):
    key = os.getenv("LTA_API_KEY")
    if not key:
        return {"source": "LTA unavailable", "speed_bands": [], "incidents": [], "missing": "未配置 LTA_API_KEY"}
    now = datetime.now(timezone.utc).isoformat()
    collected = {}
    with httpx.Client(timeout=20, trust_env=False) as client:
        for endpoint in ("v4/TrafficSpeedBands", "TrafficIncidents"):
            values = []
            for skip in range(0, 20000, 500):
                response = client.get(f"{settings.lta_base}/{endpoint}", headers={"AccountKey": key, "accept": "application/json"}, params={"$skip": skip})
                response.raise_for_status()
                batch = response.json().get("value", [])
                values.extend(batch)
                if len(batch) < 500:
                    break
            else:
                raise ToolFailure("交通数据分页超过上限，未使用不完整数据。")
            collected[endpoint] = values
    return {"source": "LTA DataMall v4", "observed_at": now, "timestamp_basis":"retrieval time",
            "speed_bands": collected["v4/TrafficSpeedBands"], "incidents": collected["TrafficIncidents"]}


def _bearing(coords):
    import math
    a, b = coords[0], coords[-1]
    return math.degrees(math.atan2(b[0]-a[0], b[1]-a[1])) % 360


def match_speed_bands(graph, records, max_distance_m=25, max_heading=30):
    project = Transformer.from_crs(4326, 32648, always_xy=True).transform
    entries = list(graph.edges(keys=True, data=True))
    geometries = [transform(project, e[3]["geometry"]) for e in entries]
    tree = STRtree(geometries)
    matches, rejected = {}, 0
    for item in records:
        try:
            if "Location" in item:
                numbers = [float(x) for x in item["Location"].split()]
            else:
                numbers = [float(item[field]) for field in ("StartLat","StartLon","EndLat","EndLon")]
            if len(numbers) < 4 or len(numbers) % 2:
                raise ValueError("Invalid coordinates")
            line = LineString([(numbers[i+1], numbers[i]) for i in range(0, len(numbers), 2)])
            metric = transform(project, line)
            for index in tree.query(metric.buffer(max_distance_m)):
                u, v, k, edge = entries[index]
                shape = geometries[index]
                # Check full edge overlap and direction, rather than nearest point alone.
                heading = abs((_bearing(list(shape.coords))-_bearing(list(metric.coords))+180) % 360-180)
                overlap = shape.intersection(metric.buffer(max_distance_m)).length / max(shape.length, 1)
                name = str(edge.get("name", "")).casefold()
                road_name = str(item.get("RoadName", "")).casefold()
                if heading > max_heading or overlap < 0.8 or (name and road_name and road_name not in name):
                    continue
                confidence = overlap * (1-heading/180)
                previous = matches.get(edge["edge_id"])
                if not previous or confidence > previous["confidence"]:
                    matches[edge["edge_id"]] = {"min_speed": float(item["MinimumSpeed"]),
                        "max_speed": float(item["MaximumSpeed"]), "confidence": confidence, "link_id": item.get("LinkID")}
        except (KeyError, ValueError, TypeError):
            rejected += 1
    return matches, rejected


def apply_traffic(graph, payload, departure, *, replay=False, max_age_s=600):
    # Reset every attempt: traffic from a previous context must not leak.
    for _, _, _, edge in graph.edges(keys=True, data=True):
        edge.update(travel_time=edge["base_time_s"], traffic_known=False, congested=False,
                    time_lower_s=edge["base_time_s"], time_upper_s=None)
    observed = payload.get("observed_at")
    fresh = False
    if observed:
        stamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        fresh = abs((departure-stamp).total_seconds()) <= max_age_s
    matches = payload.get("edge_speeds", {}) if replay else {}
    rejected = 0
    if not matches and fresh:
        matches, rejected = match_speed_bands(graph, payload.get("speed_bands", []))
    if not fresh:
        matches = {}
    known = 0
    for u, v, k, edge in graph.edges(keys=True, data=True):
        record = matches.get(edge["edge_id"])
        if record:
            low, high = float(record["min_speed"]), float(record["max_speed"])
            if low < 0 or high <= 0 or low > high or record.get("confidence", 1) < 0.8:
                continue
            mid = max((low+high)/2, 1)
            estimate = edge["length"] / (mid/3.6)
            edge.update(travel_time=estimate, traffic_known=True,
                time_lower_s=edge["length"]/(high/3.6),
                time_upper_s=edge["length"]/(low/3.6) if low > 0 else None,
                congested=estimate > edge["base_time_s"]*1.3,
                traffic_link_id=record.get("link_id"))
            known += 1
    # Only explicit directed closures from a curated replay are removed. A point
    # incident from live LTA is a risk observation, not proof an edge is closed.
    closures = payload.get("closed_edges", []) if replay else []
    for _, _, _, edge in graph.edges(keys=True, data=True):
        if edge["edge_id"] in closures:
            edge["closed"] = True
    return {"matched_edges": known, "total_edges": graph.number_of_edges(), "fresh": fresh,
            "observed_at": observed, "rejected_records": rejected,
            "incidents": payload.get("incidents", []), "source": payload.get("source", "unknown"),
            "missing": payload.get("missing"), "timestamp_basis":payload.get("timestamp_basis","snapshot observation")}
