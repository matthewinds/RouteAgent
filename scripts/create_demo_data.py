"""Create labelled SYNTHETIC fixtures; no real-world travel accuracy is claimed."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    nodes = [{"id": 1, "lat": 1.2935, "lon": 103.852}, {"id": 2, "lat": 1.2985, "lon": 103.843},
             {"id": 3, "lat": 1.3048, "lon": 103.832}, {"id": 4, "lat": 1.298, "lon": 103.844},
             {"id": 5, "lat": 1.310, "lon": 103.838}]
    edges = []
    def add(u, v, length, seconds, highway="residential"):
        edges.append({"u":u, "v":v, "length": length, "base_time_s":seconds, "highway":highway})
    for u,v,d,t,h in [(1,2,900,240,"primary"),(2,3,1300,360,"motorway"),
                        (1,4,1100,330,"residential"),(4,3,1300,360,"residential"),
                        (1,5,1500,420,"residential"),(5,3,1400,420,"residential"),
                        (2,4,200,80,"residential")]:
        add(u,v,d,t,h)
        add(v,u,d,t,h)
    places = [{"id":"city-hall", "name":"City Hall", "lat":nodes[0]["lat"], "lon":nodes[0]["lon"], "source":"synthetic landmark"},
              {"id":"orchard", "name":"Orchard Road", "lat":nodes[2]["lat"], "lon":nodes[2]["lon"], "source":"synthetic landmark"},
              {"id":"cafe-central", "name":"Central Cafe", "lat":nodes[3]["lat"], "lon":nodes[3]["lon"], "category":"cafe", "opening_hours":"24/7", "source":"synthetic POI"},
              {"id":"cafe-scenic", "name":"Garden Cafe", "lat":nodes[4]["lat"], "lon":nodes[4]["lon"], "category":"cafe", "opening_hours":"08:00-20:00", "source":"synthetic POI"}]
    speeds = {f"{e['u']}:{e['v']}:0":{"min_speed":e["length"]/e["base_time_s"]*3.6,
        "max_speed":e["length"]/e["base_time_s"]*3.6, "confidence":1} for e in edges}
    payload = {"metadata":{"synthetic":True,"graph_source":"synthetic Singapore-coordinate test graph",
        "description":"合成机制演示，不是真实道路、店铺或交通导航", "observed_at":"2026-10-01T09:00:00+08:00"},
        "nodes":nodes,"edges":edges,"places":places,
        "traffic":{"source":"synthetic speed snapshot", "observed_at":"2026-10-01T09:00:00+08:00", "edge_speeds":speeds,"incidents":[]}}
    destination = ROOT / "data/snapshots"
    destination.mkdir(parents=True, exist_ok=True)
    def write(name, value):
        (destination / (name+".json")).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    write("demo-clear",payload)
    traffic = copy.deepcopy(payload)
    for edge_id in ("1:2:0","2:3:0","1:4:0","4:3:0"):
        for field in ("min_speed","max_speed"):
            traffic["traffic"]["edge_speeds"][edge_id][field] /= 2.5
    write("demo-traffic", traffic)
    unknown = copy.deepcopy(payload)
    unknown["traffic"]["edge_speeds"] = {}
    write("demo-unknown",unknown)
    closure = copy.deepcopy(payload)
    closure["traffic"]["closed_edges"] = ["1:2:0","1:4:0"]
    write("demo-closure",closure)
    replan = copy.deepcopy(payload)
    replan["places"] = [p for p in places if not p.get("category")]
    for i in range(11):
        node = 20+i
        lat,lon = (1.297+i*.0001,103.845+i*.0001) if i<10 else (1.311,103.843)
        replan["nodes"].append({"id":node,"lat":lat,"lon":lon})
        replan["places"].append({"id":f"cafe-{i}", "name":f"Candidate Cafe {i}", "lat":lat,"lon":lon,
                                "category":"cafe", "opening_hours":"24/7","source":"synthetic POI"})
        for u,v in [(1,node),(node,3)]:
            seconds = 1100 if i<10 else 300
            replan["edges"].append({"u":u,"v":v,"length":1000,"base_time_s":seconds,"highway":"residential"})
            speed = 1000/seconds*3.6
            replan["traffic"]["edge_speeds"][f"{u}:{v}:0"] = {"min_speed":speed,"max_speed":speed,"confidence":1}
    write("demo-replan", replan)
    cases = []
    for split, count in [("dev",20),("test",60)]:
        group_size = count//5
        for i in range(count):
            group = i//group_size
            budget = (50 if split == "dev" else 25)+(i%group_size)
            if group == 0:
                text = f"现在驾车从 City Hall 到 Orchard Road，{budget} 分钟内到达。"
                snapshot, expected = "demo-clear", "verified"
            elif group == 1:
                text = f"现在驾车从 City Hall 到 Orchard Road，{budget} 分钟内到达，必须经过咖啡店，停留 5 分钟，希望避开拥堵。"
                snapshot, expected = "demo-traffic", "verified"
            elif group == 2:
                budget = (24 if split == "dev" else 18)+(i%group_size)*.5
                stop = 4 if split == "dev" else 5
                text = f"现在驾车从 City Hall 到 Orchard Road，{budget:g} 分钟内到达，必须经过咖啡店，停留 {stop} 分钟。"
                snapshot, expected = "demo-replan", "verified"
            elif group == 3:
                budget = (3 if split == "dev" else 5)+(i%group_size)*.1
                text = f"现在驾车从 City Hall 到 Orchard Road，{budget:g} 分钟内到达，必须经过咖啡店，停留 10 分钟。"
                snapshot, expected = "demo-clear", "search_exhausted"
            else:
                budget = (50 if split == "dev" else 30)+(i%group_size)*.1
                preference = "希望经过" if split == "dev" else "必须经过"
                text = f"现在驾车从 City Hall 到 Orchard Road，{budget:g} 分钟内到达。" if i%2 else f"从 City Hall 到 Orchard Road，{budget:g} 分钟内到达，{preference}咖啡店。"
                snapshot = "demo-unknown" if i%2 else "demo-clear"
                expected = "unverified" if i%2 else "needs_clarification"
            case = {"id":f"{split}-{i+1:03d}", "group":["direct","traffic_poi","replan","infeasible","unknown_or_clarify"][group],
                    "text":text,"snapshot_id":snapshot,"expected_status":expected,
                    "clarifications":{"departure_time":"2026-10-01T09:00:00+08:00"}, "synthetic":True}
            has_poi = group in {1, 2, 3} or (group == 4 and i%2 == 0)
            case["gold_request"] = {"origin": "City Hall", "destination": "Orchard Road",
                "mode": None if group == 4 and i%2 == 0 else "driving", "max_duration_s": budget*60,
                "poi_category": "cafe" if has_poi else None,
                "poi_required": has_poi and not (group == 4 and i%2 == 0 and split == "dev"),
                "stop_duration_s": 300 if group == 1 else (240 if split == "dev" else 300) if group == 2 else 600 if group == 3 else None,
                "prefer_avoid_congestion": group == 1, "avoid_highways": False}
            cases.append(case)
        folder = ROOT / "data/requests"
        folder.mkdir(parents=True,exist_ok=True)
        (folder / (split+".json")).write_text(json.dumps(cases[-count:],ensure_ascii=False,indent=2),encoding="utf-8")
    print("Created 5 labelled synthetic snapshots, 20 dev and 60 test requests.")


if __name__ == "__main__":
    main()
