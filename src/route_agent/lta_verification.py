"""Bind LTA observations to actual service legs; never invent a revised schedule."""
import re
from datetime import datetime, timedelta
from .models import CheckResult
from .providers import distance, now_utc
from .traffic import future_departure, past_departure

LINE_CODES={"EW":"EWL","NS":"NSL","NE":"NEL","CC":"CCL","CE":"CEL","CG":"CGL","DT":"DTL","TE":"TEL","BP":"BPL","SE":"SLRT","SW":"SLRT","PE":"PLRT","PW":"PLRT"}

def line_code(leg):
    return LINE_CODES.get(leg.service_name,leg.service_name if leg.service_name in set(LINE_CODES.values()) else None)

def live_query(queries, dataset, evidence, parameters=None):
    for query in reversed(queries):
        if query.get("dataset")!=dataset or any(str(query.get("parameters",{}).get(k))!=str(v) for k,v in (parameters or {}).items()):continue
        refs=query.get("evidence_ids",[])
        if refs and all(evidence.get(e) and evidence[e].valid_until and evidence[e].valid_until>=now_utc() for e in refs):return query
    return None

def stop_code(leg, queries):
    if leg.origin.stop_code and re.fullmatch(r"\d{5}",leg.origin.stop_code):return leg.origin.stop_code
    matches=set()
    for query in queries:
        if query.get("dataset")!="bus_stops":continue
        for stop in query.get("records",[]):
            try:
                if str(stop.get("Description","")).casefold()==leg.origin.name.casefold() and distance(
                    [float(stop["Longitude"]),float(stop["Latitude"])],[leg.origin.lon,leg.origin.lat])<=100:
                    code=str(stop["BusStopCode"])
                    if re.fullmatch(r"\d{5}",code):matches.add(code)
            except (KeyError,TypeError,ValueError):continue
    return next(iter(matches)) if len(matches)==1 else None

def operational_checks(route, request, queries, evidence):
    checks=[];required=[];advisories=[]
    def add(constraint,state,reason,actual=None,refs=None,hard=False):
        checks.append(CheckResult(constraint=constraint,status=state,reason=reason,actual=actual,evidence_ids=refs or [],hard=hard))
    if future_departure(request.departure_time) or past_departure(request.departure_time):
        add("lta_current_reference","unknown","当前 LTA 观测不适用于该出发时段；保留服务时刻，临近出发再核对。")
        return checks,required,advisories
    rails=[leg for leg in route.legs if leg.mode=="rail"]
    buses=[leg for leg in route.legs if leg.mode=="bus"]
    if rails:
        query=live_query(queries,"train_alerts",evidence)
        if query is None:
            required.append({"dataset":"train_alerts"})
            add("lta_train_status","unknown","尚无有效 LTA 地铁运营通告，不能声称已核对实时中断。")
        else:
            status=query["records"][0];affected=status.get("AffectedSegments",[])
            if str(status.get("Status"))=="1":
                add("lta_train_status","pass","LTA 当前报告正常服务或轻微延误；不是后续运行的准时保证。",status.get("Status"),query["evidence_ids"])
            else:
                matched=False;uncertain=not isinstance(affected,list) or not affected
                if not isinstance(affected,list):affected=[]
                for leg in rails:
                    line=line_code(leg)
                    if not line:uncertain=True;continue
                    for segment in affected:
                        if not isinstance(segment,dict):uncertain=True;continue
                        if segment.get("Line")!=line:continue
                        codes=set(re.findall(r"[A-Z]{1,3}\d+[A-Z]?",str(segment.get("Stations",""))))
                        endpoints={leg.origin.stop_code,leg.destination.stop_code}-{None}
                        # Partial line disruptions: endpoints do not prove the interior is unaffected.
                        if codes and codes.intersection(endpoints):matched=True
                        else:uncertain=True
                add("lta_train_status","fail" if matched else "unknown" if uncertain else "pass",
                    "LTA 报告该行程的站点/线路有中断或重大延误；需要换站或换方式重新规划。" if matched else
                    "该线路存在中断，但未核实经过的站序/方向，不能把未匹配到端点当成不受影响。" if uncertain else
                    "当前 LTA 通告的受影响线路与已识别的行程线路不同。",affected,query["evidence_ids"],hard=matched or uncertain)
    for index,leg in enumerate(buses):
        code=stop_code(leg,queries)
        constraint="lta_bus_arrival_"+str(index+1)
        if not code:
            required.append({"dataset":"bus_stops","filters":{"Description":leg.origin.name}})
            add(constraint,"unknown","公交上车站缺少可唯一核对的官方站号；先查询站点资料，不能用附近站号代替。")
            continue
        query=live_query(queries,"bus_arrival",evidence,{"BusStopCode":code})
        # A query may explicitly filter a different service. Do not reuse it.
        if query and query.get("parameters",{}).get("ServiceNo") not in (None,leg.service_name):query=None
        if query is None:
            required.append({"dataset":"bus_arrival","parameters":{"BusStopCode":code,"ServiceNo":leg.service_name}})
            add(constraint,"unknown","该站点/线路未取得有效公交到站观测；OneMap 时刻仍是服务估计。")
            continue
        arrivals=[]
        ready=route.legs[route.legs.index(leg)-1].arrival_time if route.legs.index(leg)>0 else request.departure_time
        for service in query["records"]:
            if str(service.get("ServiceNo"))!=leg.service_name:continue
            for key in ("NextBus","NextBus2","NextBus3"):
                bus=service.get(key) or {}
                try:
                    arrival=datetime.fromisoformat(bus.get("EstimatedArrival","").replace("Z","+00:00"))
                    if arrival.tzinfo and ready and arrival>=ready:
                        arrivals.append({"estimated_arrival":arrival.isoformat(),"load":bus.get("Load"),"wheelchair":bus.get("Feature"),"monitored":bus.get("Monitored"),"visit_number":bus.get("VisitNumber")})
                except (ValueError,TypeError):continue
        compatible=[a for a in arrivals if leg.departure_time and abs((datetime.fromisoformat(a["estimated_arrival"])-leg.departure_time).total_seconds())<=120]
        add(constraint,"pass" if compatible else "unknown",
            "LTA 到站参考与预计上车时刻接近；未核对同一车辆/方向与后续接驳，不据此替换整程时间。" if compatible else
            "LTA 当前后续三班观测未覆盖或未匹配预计上车时刻；缺测不等于停运，可重新查询完整公共交通候选。",
            {"stop_code":code,"service":leg.service_name,"reachable_arrivals":arrivals},query["evidence_ids"])
    # Optional datasets remain route-specific advisories, never fabricated minutes.
    for query in queries:
        if not live_query([query],query.get("dataset"),evidence):continue
        if query["dataset"] in ("station_crowd","station_crowd_forecast","facilities_maintenance"):
            stations={x.stop_code for leg in rails for x in (leg.origin,leg.destination)}-{None}
            rows=query["records"]
            if query["dataset"]=="station_crowd_forecast":
                rows=[{**level,"Station":station.get("Station"),"Date":day.get("Date")} for day in rows
                    for station in day.get("Stations",[]) for level in station.get("Interval",[])]
            for row in rows:
                if row.get("Station",row.get("StationCode")) not in stations:continue
                if query["dataset"] in ("station_crowd","station_crowd_forecast"):
                    try:
                        start=datetime.fromisoformat(row.get("StartTime",row.get("Start","")))
                        end=datetime.fromisoformat(row["EndTime"]) if row.get("EndTime") else start+timedelta(minutes=30)
                        if not start.tzinfo or not any(leg.departure_time and start<=leg.departure_time<end for leg in rails
                            if row["Station"] in (leg.origin.stop_code,leg.destination.stop_code)):continue
                    except (ValueError,TypeError):continue
                advisories.append({"dataset":query["dataset"],"record":row,"evidence_ids":query["evidence_ids"],"basis":query["temporal_basis"]})
        elif query["dataset"] in ("traffic_incidents","faulty_lights","flood_alerts","road_works","traffic_advisories"):
            # Keep textual advisories for agent review. Geographic matching must use
            # real geometry; a PUB broadcast circle is NOT the flooded footprint.
            advisories.append({"dataset":query["dataset"],"records":query["records"][:20],"evidence_ids":query["evidence_ids"],"basis":"需要按实际路线/生效时段核对；广播范围不等于积水范围，未推断延误分钟数。"})
    return checks,required,advisories
