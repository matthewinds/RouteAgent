"""Download and version a bounded Singapore OSM driving graph."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
import osmnx as ox
from route_agent.config import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox",nargs=4,type=float,default=[103.825,1.28,103.86,1.315],metavar=("WEST","SOUTH","EAST","NORTH"))
    args = parser.parse_args()
    west,south,east,north = args.bbox
    if not (103.5 <= west < east <= 104.2 and 1.15 <= south < north <= 1.5):
        parser.error("Bounds must be inside the Singapore case-study region")
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(ROOT/"data/cache/osmnx")
    ox.settings.requests_timeout = 60
    ox.settings.requests_kwargs = {"proxies":{"http":"","https":""}}
    graph = ox.graph_from_bbox((west,south,east,north),network_type="drive",retain_all=True)
    graph = ox.routing.add_edge_speeds(graph)
    graph = ox.routing.add_edge_travel_times(graph)
    destination = ROOT/"data/osm"
    destination.mkdir(parents=True,exist_ok=True)
    path = destination/"singapore.graphml"
    ox.save_graphml(graph,path)
    meta = {"source":"OpenStreetMap via OSMnx", "created_utc":datetime.now(timezone.utc).isoformat(),
        "bbox":args.bbox, "nodes":graph.number_of_nodes(), "edges":graph.number_of_edges(),
        "sha256":hashlib.sha256(path.read_bytes()).hexdigest(), "synthetic":False,
        "scope":"Singapore central-area case study; outside bounds not supported",
        "travel_time":"OSM speed-tag / imputed FREE-FLOW estimate; not real-time traffic"}
    (destination/"metadata.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")
    print(json.dumps(meta,indent=2))


if __name__ == "__main__":
    main()
