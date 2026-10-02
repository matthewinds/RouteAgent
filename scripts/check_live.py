"""Credential presence and explicitly requested real smoke tests; never fake acceptance."""
import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from route_agent.config import Settings
from route_agent.tools import Budget, ToolFailure
from route_agent.providers import Providers
from route_agent import plan_route

def main():
    parser=argparse.ArgumentParser(description="API setup check; presence alone is not connectivity.")
    parser.add_argument("--smoke",action="store_true",help="Actually call configured providers; consumes quotas.")
    parser.add_argument("--public",action="store_true",help="Only query public OSM and Open-Meteo, never paid/keyed providers.")
    parser.add_argument("--routing",action="store_true",help="Only check real OSRM driving, walking and motorway exclusion; no model or geocoder calls.")
    parser.add_argument("--traffic",action="store_true",help="Check real LTA pagination and matching against one OSRM route; no model/geocoder calls.")
    parser.add_argument("--plan",action="store_true",help="Actually run a DeepSeek walking plan; may incur cost.")
    args=parser.parse_args()
    settings=Settings()
    report={"checked_at":datetime.now(ZoneInfo("Asia/Singapore")).isoformat(),
            "configured":settings.readiness(),"routing":settings.routing_configuration(),"connectivity":{},"acceptance":"未验收"}
    if args.smoke or args.public or args.routing or args.traffic:
        providers=Providers(settings,Budget(settings))
        from route_agent.models import Place
        public_place=Place(id="public-cityhall",name="City Hall area",lon=103.85,lat=1.29,source="固定公共测试坐标")
        public_end=Place(id="public-marina",name="Marina Bay area",lon=103.86,lat=1.30,source="固定公共测试坐标")
        routes={"OSRM driving":lambda:providers.routes(public_place,public_end,"driving"),
                "OSRM walking":lambda:providers.routes(public_place,public_end,"walking"),
                "OSRM avoid motorways":lambda:providers.routes(public_place,public_end,"driving",True)}
        checks={"ORS geocoding":lambda:providers.geocode("City Hall, Singapore"),**routes,
                "Open-Meteo":lambda:providers.weather([[103.85,1.29]]),
                "OSM roads":lambda:providers.roads(public_place,200,10),
                "OSM hospitals":lambda:providers.pois(public_place,"hospital",1500,10),
                "OSM restaurants":lambda:providers.pois(public_place,"restaurant",300,10),
                "LTA":providers.traffic}
        if args.public:
            checks={k:v for k,v in checks.items() if k not in ("ORS geocoding","LTA") and not k.startswith("OSRM")}
        if args.routing:
            checks=routes
        if args.traffic:
            checks={"LTA":providers.traffic,"OSRM driving":routes["OSRM driving"]}
        traffic_payload = None
        for name,check in checks.items():
            if name=="ORS geocoding" and not settings.credential("ORS_API_KEY") or name=="LTA" and not settings.credential("LTA_API_KEY"):
                report["connectivity"][name]={"status":"未验收","reason":"缺少 Key"}; continue
            try:
                hits_before=providers.cache_hits
                value=check()
                count=len(value) if isinstance(value,list) else len(value.get("roads",value.get("points",[]))) if isinstance(value,dict) else 0
                report["connectivity"][name]={"status":"API 响应成功","cached":providers.cache_hits>hits_before,
                    "note":"单个响应不代表完整规划验收","item_count":count}
                if name.startswith("OSRM"):
                    report["connectivity"][name]["routes"]=[v.model_dump(mode="json") for v in value]
                    if traffic_payload:
                        from route_agent.traffic import apply_traffic
                        apply_traffic(value,traffic_payload,datetime.now(ZoneInfo("Asia/Singapore")),budget=providers.budget)
                        report["route_traffic"]=[{"id":v.id,"coverage":v.traffic_coverage,
                            "duration_s":v.traffic_duration_s,"lower_s":v.traffic_lower_s,"upper_s":v.traffic_upper_s} for v in value]
                if name=="LTA":
                    traffic_payload=value
                    report["connectivity"][name]["collection"]=value.get("collection",{})
                    report["connectivity"][name]["speed_band_count"]=len(value["speed_bands"])
            except Exception as error:
                report["connectivity"][name]={"status":"失败","error_type":type(error).__name__,
                    "error_code":error.code if isinstance(error,ToolFailure) else "unexpected_error",
                    "message":str(error) if isinstance(error,ToolFailure) else "响应无法使用"}
        report["evidence"]=[e.model_dump(mode="json") for e in providers.evidence.values()]
    if args.plan:
        result=plan_route("现在从 City Hall 步行到 Marina Bay，不需要经停。",
            {"departure_time":datetime.now(ZoneInfo("Asia/Singapore")).isoformat()})
        report["planning"]={"status":result.status,"message":result.message,"run_id":result.run_id}
    report["http_calls"]=providers.budget.http_calls if args.smoke or args.public or args.routing or args.traffic else 0
    directory=settings.root/("outputs/traffic-check" if args.traffic else "outputs/osrm-check" if args.routing else "outputs/public-check" if args.public else "outputs/online-check")
    directory.mkdir(parents=True,exist_ok=True)
    (directory/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
    compact={k:v for k,v in report.items() if k!="evidence"}
    compact["connectivity"]={name:{k:v for k,v in check.items() if k!="routes"} for name,check in report["connectivity"].items()}
    print(json.dumps(compact,ensure_ascii=False,indent=2))
    return (1 if any(v["status"]=="失败" for v in report["connectivity"].values()) else 0) if args.routing or args.public or args.traffic else 2 if settings.missing_required() else 0

if __name__=="__main__": raise SystemExit(main())
