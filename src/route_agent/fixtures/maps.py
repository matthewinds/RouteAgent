"""Replay and live OSM adapters. Road access is explicit, not assumed."""
import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
import httpx
import networkx as nx
from shapely.geometry import LineString
from .cache import JsonCache
from .models import Place
from .tools import ToolFailure

_geocode_lock = threading.Lock()
_geocode_last = 0.0


def distance_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2-lat1, lon2-lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def nearest_node(graph, place, max_distance=150):
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    if "_nearest_index" not in graph.graph:
        ids = list(graph.nodes)
        graph.graph["_nearest_index"] = (ids, STRtree([Point(graph.nodes[n]["x"], graph.nodes[n]["y"]) for n in ids]))
    ids, index = graph.graph["_nearest_index"]
    node = ids[int(index.nearest(Point(place.lon, place.lat)))]
    snap = distance_m((place.lat, place.lon), (graph.nodes[node]["y"], graph.nodes[node]["x"]))
    if snap > max_distance:
        raise ToolFailure(f"{place.name} 距离当前驾车路网入口过远；请提供可到达的道路入口。")
    return place.model_copy(update={"node": node, "snap_m": snap})


def normalize_graph(graph):
    for u, v, k, edge in graph.edges(keys=True, data=True):
        edge["edge_id"] = f"{u}:{v}:{k}"
        edge["length"] = float(edge["length"])
        edge["base_time_s"] = float(edge.get("base_time_s", edge.get("travel_time", 0)))
        if edge["base_time_s"] <= 0:
            raise ToolFailure("路网缺少有效的基础时间，请重新准备地图。")
        edge["travel_time"] = edge["base_time_s"]
        edge["traffic_known"] = False
        edge["congested"] = False
        edge["closed"] = bool(edge.get("closed", False))
        edge["highway_route"] = any(x in str(edge.get("highway", "")) for x in ("motorway", "trunk"))
        if "geometry" not in edge:
            edge["geometry"] = LineString([(graph.nodes[u]["x"], graph.nodes[u]["y"]), (graph.nodes[v]["x"], graph.nodes[v]["y"])])
        else:
            coords = list(edge["geometry"].coords)
            start = (graph.nodes[u]["x"], graph.nodes[u]["y"])
            if math.dist(start, coords[-1]) < math.dist(start, coords[0]):
                edge["geometry"] = LineString(coords[::-1])
    return graph


@dataclass
class MapContext:
    graph: nx.MultiDiGraph
    adapter: object
    metadata: dict
    traffic: dict | None = None


class ReplayAdapter:
    def __init__(self, payload, graph, settings):
        self.payload, self.graph, self.settings = payload, graph, settings
        self.places = [Place(**x) for x in payload["places"]]

    def geocode(self, query):
        query = query.casefold().strip()
        exact = [p for p in self.places if query == p.name.casefold() or query == p.id.casefold()]
        matches = exact or [p for p in self.places if query in p.name.casefold()]
        if len(matches) > 1:
            raise ToolFailure("地点名称有多个匹配，请输入完整地点名称。")
        if not matches:
            raise ToolFailure("此快照不包含该地点，请选择示例地点，或切换真实地图模式。")
        return nearest_node(self.graph, matches[0], self.settings.max_snap_m)

    def search_pois(self, category=None, name=None):
        if name:
            return [self.geocode(name)]
        places = [p for p in self.places if p.category == category]
        return [nearest_node(self.graph, p, self.settings.max_snap_m) for p in places]

    def traffic_data(self):
        return self.payload.get("traffic", {})


class LiveOSMAdapter:
    def __init__(self, graph, settings):
        self.graph, self.settings = graph, settings
        self.cache = JsonCache(settings.root / "data/cache")

    def geocode(self, query):
        global _geocode_last
        import re
        coordinate = re.fullmatch(r"\s*(1\.\d+)\s*,\s*(103\.\d+|104\.\d+)\s*", query)
        if coordinate:
            return nearest_node(self.graph, Place(id=query, name=query, lat=float(coordinate[1]),
                lon=float(coordinate[2]), source="user coordinates"), self.settings.max_snap_m)
        args = {"q": query, "countrycodes": "sg", "format": "jsonv2", "limit": 5}
        result = self.cache.get("geocode", args, 30*86400)
        if result is None:
            with _geocode_lock:
                wait = 1.1 - (time.monotonic()-_geocode_last)
                if wait > 0:
                    time.sleep(wait)
                with httpx.Client(timeout=20, trust_env=False) as client:
                    response = client.get("https://nominatim.openstreetmap.org/search", params=args,
                        headers={"User-Agent": "MapAgents-FYP/0.1 (local academic prototype)"})
                    response.raise_for_status()
                    result = response.json()
                _geocode_last = time.monotonic()
                self.cache.put("geocode", args, result)
        if not result:
            raise ToolFailure("没有找到这个新加坡地点，请补充地址。")
        unique = {(round(float(x["lat"]), 4), round(float(x["lon"]), 4)) for x in result}
        if len(unique) > 1:
            names = [x["display_name"].split(",")[0] for x in result[:3]]
            raise ToolFailure("地点存在歧义，请提供地址或邮编：" + "、".join(names))
        item = result[0]
        # A single fuzzy geocoder hit can still be the wrong business. Require a
        # recognizable short-name or postal-code match before resolving it.
        clean = lambda value: re.sub(r"[^\w]+", "", value.casefold().replace("singapore", ""))
        query_name, short_name = clean(query), clean(item["display_name"].split(",")[0])
        postcode = re.search(r"\b\d{6}\b", query)
        if not ((query_name and short_name and (query_name in short_name or short_name in query_name)) or
                (postcode and postcode[0] in item["display_name"])):
            raise ToolFailure("查询返回的名称尚不能确认："+item["display_name"]+
                "。请核对后填写更明确的地点名称或纬度,经度。")
        place = Place(id=f"{item['osm_type']}:{item['osm_id']}", name=item["display_name"],
            lat=float(item["lat"]), lon=float(item["lon"]), source="OSM/Nominatim")
        return nearest_node(self.graph, place, self.settings.max_snap_m)

    def search_pois(self, category=None, name=None):
        if name:
            return [self.geocode(name)]
        nodes = self.graph.nodes
        south, north = min(x["y"] for _, x in nodes(data=True)), max(x["y"] for _, x in nodes(data=True))
        west, east = min(x["x"] for _, x in nodes(data=True)), max(x["x"] for _, x in nodes(data=True))
        if category not in {"cafe", "restaurant", "hospital"}:
            raise ToolFailure("首版支持咖啡店、餐厅和医院，请明确地点类别。")
        query = f'[out:json][timeout:20];nwr["amenity"="{category}"]({south},{west},{north},{east});out center tags;'
        result = self.cache.get("overpass", {"query": query}, 7*86400)
        if result is None:
            import osmnx as ox
            ox.settings.cache_folder = str(self.settings.root / "data/cache/osmnx")
            ox.settings.requests_timeout = 30
            ox.settings.requests_kwargs = {"proxies": {"http": "", "https": ""}}
            features = ox.features.features_from_bbox((west, south, east, north), {"amenity": category})
            elements = []
            for (kind, identifier), item in features.iterrows():
                center = item.geometry.centroid
                tags = {key: item[key] for key in ("name", "opening_hours")
                        if key in item and isinstance(item[key], str)}
                elements.append({"type": kind, "id": int(identifier), "lat": center.y,
                                 "lon": center.x, "tags": tags})
            result = {"elements": elements}
            self.cache.put("overpass", {"query": query}, result)
        places = []
        for item in result.get("elements", []):
            coord = item.get("center", item)
            tags = item.get("tags", {})
            if "lat" not in coord or "lon" not in coord:
                continue
            place = Place(id=f"{item['type']}:{item['id']}", name=tags.get("name", f"{category} {item['id']}"),
                lat=coord["lat"], lon=coord["lon"], category=category, opening_hours=tags.get("opening_hours"))
            try:
                places.append(nearest_node(self.graph, place, self.settings.max_snap_m))
            except ToolFailure:
                continue
        return places

    def traffic_data(self):
        from .traffic import fetch_lta
        return fetch_lta(self.settings)


def load_context(mode, snapshot_id, settings):
    if mode == "replay":
        path = settings.snapshots / f"{snapshot_id}.json"
        if not path.resolve().is_relative_to(settings.snapshots.resolve()):
            raise ToolFailure("无效的快照名称。")
        if not path.exists():
            raise ToolFailure("未找到这个数据快照。")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "graph_file" in payload:
            import osmnx as ox
            graph_path = (settings.root / payload["graph_file"]).resolve()
            if not graph_path.is_relative_to((settings.root / "data").resolve()):
                raise ToolFailure("快照路网路径无效。")
            graph = ox.load_graphml(graph_path)
            expected = payload.get("graph_sha256")
            if expected and hashlib.sha256(graph_path.read_bytes()).hexdigest() != expected:
                raise ToolFailure("快照路网版本不匹配。")
        else:
            graph = nx.MultiDiGraph(crs="epsg:4326")
            for node in payload["nodes"]:
                graph.add_node(node["id"], x=node["lon"], y=node["lat"])
            for edge in payload["edges"]:
                attrs = {k: v for k, v in edge.items() if k not in {"u", "v", "key"}}
                graph.add_edge(edge["u"], edge["v"], key=edge.get("key", 0), **attrs)
        graph = normalize_graph(graph)
        meta = {**payload["metadata"], "mode": mode, "snapshot_id": snapshot_id,
                "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return MapContext(graph, ReplayAdapter(payload, graph, settings), meta, payload.get("traffic"))
    if mode != "live":
        raise ToolFailure("请选择实时或重放模式。")
    if not settings.graph_path.exists():
        raise ToolFailure("尚未准备真实 OSM 路网，请先运行地图准备工具。")
    import osmnx as ox
    graph = normalize_graph(ox.load_graphml(settings.graph_path))
    graph_hash = hashlib.sha256(settings.graph_path.read_bytes()).hexdigest()
    return MapContext(graph, LiveOSMAdapter(graph, settings),
        {"mode": "live", "synthetic": False, "graph_source": "OpenStreetMap",
         "graph_sha256": graph_hash, "scope": "prepared Singapore road graph"})
