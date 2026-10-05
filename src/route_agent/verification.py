"""Publish guards evaluate provider facts, never the model's claimed feasibility."""
import re
from datetime import timedelta
from .models import CheckResult
from .providers import distance, location_name_matches
from .traffic import future_departure, past_departure
def check(name,state,reason,actual=None,required=None,refs=None,hard=True):
    return CheckResult(constraint=name,status=state,reason=reason,actual=actual,required=required,evidence_ids=refs or [],hard=hard)

def time_bounds(route):
    lower = upper = route.stop_s+(route.parking_s or 0)
    if route.parking is not None and route.parking_s is None:
        return None,None
    for leg in route.legs:
        if leg.mode=="driving":
            if leg.traffic_lower_s is None or leg.traffic_upper_s is None:
                return None,None
            lower+=leg.traffic_lower_s
            upper+=leg.traffic_upper_s
        else:
            lower+=leg.provider_duration_s
            upper+=leg.provider_duration_s
    return lower,upper

def verify_route(route,request,evidence=None):
    from .providers import now_utc
    # Recheck freshness at publication; never retain an expired complete ETA.
    stale = any(leg.mode=="driving" and leg.traffic_retrieved_at and
        (now_utc()-leg.traffic_retrieved_at).total_seconds()>600 for leg in route.legs)
    if stale:
        for leg in route.legs:
            if leg.mode=="driving":
                leg.traffic_duration_s = leg.traffic_lower_s = leg.traffic_upper_s = None
                leg.traffic_coverage = 0
                leg.partial_traffic_duration_s = None
                leg.congestion_fraction = None
        route.total_s = None
        route.traffic_coverage = 0
        route.congestion_fraction = None
        route.estimated_total_s = None
        route.time_basis = "OSRM 基础服务估计，交通证据已过期；非实时总 ETA"
    refs = route.evidence_ids
    effective_mode=request.mode or route.mode
    expected = ["driving"] if effective_mode=="driving" else ["walking"] if effective_mode=="walking" else ["driving","walking"]
    actual_modes = list(dict.fromkeys(x.mode for x in route.legs))
    mode_ok = actual_modes==expected
    if effective_mode=="transit":
        expected=["walking","bus","rail"]
        mode_ok=bool(set(actual_modes)&{"bus","rail"}) and set(actual_modes)<=set(expected)
    checks = [check("mode","pass" if mode_ok else "fail","核对实际分段交通方式。",actual_modes,expected,refs)]
    if request.mode is None and "driving" in actual_modes:
        checks.append(check("car_access","pass" if request.car_access is True else "fail",
            "自动规划的自驾段必须有用户确认的可用车辆。",request.car_access,True))
    if route.mode=="transit":
        timed=all(l.departure_time is not None and l.arrival_time is not None and l.departure_time<=l.arrival_time for l in route.legs)
        ordered=timed and all(a.arrival_time<=b.departure_time for a,b in zip(route.legs,route.legs[1:]))
        total=(route.legs[-1].arrival_time-request.departure_time).total_seconds() if timed else None
        timing_ok=ordered and route.legs[0].departure_time>=request.departure_time.replace(microsecond=0) and total is not None and abs(total-(route.provider_total_s or 0))<1
        if route.poi is not None:
            index=route.poi_leg_index
            stop_ok=index is not None and 0<=index<len(route.legs)-1
            if stop_ok:
                a,b=route.legs[index:index+2]
                stop_ok=timed and (b.departure_time-a.arrival_time).total_seconds()>=route.stop_s and route.stop_s==(request.stop_duration_s or 0)
                stop_ok=stop_ok and distance(a.geometry["coordinates"][-1],[route.poi.lon,route.poi.lat])<=150 and distance(b.geometry["coordinates"][0],[route.poi.lon,route.poi.lat])<=150
            timing_ok=timing_ok and stop_ok
        checks.append(check("transit_timing","pass" if timing_ok else "fail",
            "按服务时刻核对候车、换乘顺序和已确认经停时长，完整总时间不得省略等待。",refs=refs))
    pairs=list(zip(route.legs,route.legs[1:]))
    gaps = [distance(a.geometry["coordinates"][-1],b.geometry["coordinates"][0]) for a,b in pairs]
    # Native transit transfers are anchored by the service's stop identities
    # and schedule. Road centreline geometry need not touch the kerb geometry.
    # This does not authorize a connector between unrelated places/services.
    shared_stops=[a.source==b.source=="OneMap transit" and a.destination.id==b.origin.id and
        distance([a.destination.lon,a.destination.lat],[b.origin.lon,b.origin.lat])<2 and
        distance(a.geometry["coordinates"][-1],[a.destination.lon,a.destination.lat])<=150 and
        distance(b.geometry["coordinates"][0],[b.origin.lon,b.origin.lat])<=150 and
        a.arrival_time is not None and b.departure_time is not None and a.arrival_time<=b.departure_time for a,b in pairs]
    service_access=[]
    for a,b in pairs:
        sourced=all(any(evidence and evidence.get(e) and evidence[e].source in ("OneMap transit","OSRM Route") for e in leg.evidence_ids) for leg in (a,b))
        service_access.append(sourced and "OneMap transit" in (a.source,b.source) and "walking" in (a.mode,b.mode) and
            distance([a.destination.lon,a.destination.lat],[b.origin.lon,b.origin.lat])<2 and
            distance(a.geometry["coordinates"][-1],[a.destination.lon,a.destination.lat])<=150 and
            distance(b.geometry["coordinates"][0],[b.origin.lon,b.origin.lat])<=150 and
            a.arrival_time is not None and b.departure_time is not None and a.arrival_time<=b.departure_time)
    shared_stops=[native or access for native,access in zip(shared_stops,service_access)]
    unresolved=any(g>2 and not shared for g,shared in zip(gaps,shared_stops))
    # Never silently draw a straight connector or add zero-time transfers.
    checks.append(check("continuity","unknown" if unresolved else "pass",
        "API 分段入口存在未建模连接，需核实实际通行。" if unresolved else
        "核对 API 几何连接及 OneMap 同站点的服务换乘时刻；站点与道路中心线的小量偏移未补画虚构连接。",
        gaps,"几何≤2米，或 OneMap 同站点及有效换乘时序",refs))
    if request.poi_required:
        ok = route.poi is not None and (not request.poi_category or route.poi.category==request.poi_category)
        if request.poi_name and route.poi:
            ok &= location_name_matches(request.poi_name,route.poi.name,*route.poi.names)
        checks.append(check("required_poi","pass" if ok else "fail","按真实 POI 身份与经停段检查。",route.poi.name if route.poi else None,request.poi_name or request.poi_category,refs))
    if request.avoid_highways:
        legs = [x for x in route.legs if x.mode=="driving"]
        state = "fail" if any(x.highways is True for x in legs) else "unknown" if any(x.highways is None for x in legs) else "pass"
        checks.append(check("avoid_highways",state,"使用 OSRM 避开高速选项的服务证据；未返回证据不能确认。",refs=refs))
    if request.max_walking_m is not None:
        checks.append(check("walking_distance","pass" if route.walking_m<=request.max_walking_m else "fail",
            "累计所有步行段的 API 距离。",route.walking_m,request.max_walking_m,refs))
    lower,upper = time_bounds(route)
    if route.mode=="transit":
        lower=upper=route.provider_total_s
    limits = []
    if request.max_duration_s is not None:
        limits.append(("duration",request.max_duration_s))
    if request.arrival_deadline:
        limits.append(("arrival_deadline",(request.arrival_deadline-request.departure_time).total_seconds()))
    for name,limit in limits:
        if route.stop_s+(route.parking_s or 0)>limit:
            state,reason = "fail","仅用户确认的停留和停车预留就已超出限制。"
        elif lower is None:
            state = "unknown"
            reason = ("当前路况不能核实预约出发时段的到达时间；目前只有基础行程估计。"
                if future_departure(request.departure_time) else
                "出发时间已早于实时路况有效窗口，当前数据不能还原该时段；目前只有基础行程估计。"
                if past_departure(request.departure_time) else
                "缺少完整驾车交通时间区间或停车预留，不能核实总时间。")
        elif lower>limit:
            state,reason = "fail","服务时间估计的乐观值仍超出限制。"
        elif upper<=limit:
            state,reason = "pass","按 OneMap 含候车与换乘的服务估计及用户预留满足；不保证实际准时。" if route.mode=="transit" else "按 LTA 速度区间／OSRM 步行服务估计和用户预留满足；不保证实际准时。"
        else:
            state,reason = "unknown","时间估计区间跨越限制。"
        checks.append(check(name,state,reason,route.total_s,limit,refs))
    if request.max_congestion_fraction is not None:
        state = "unknown" if route.traffic_coverage is None or route.traffic_coverage<1-1e-6 or route.congestion_fraction is None else (
            "pass" if route.congestion_fraction<=request.max_congestion_fraction else "fail")
        checks.append(check("congestion",state,"仅按有效匹配的 LTA 路段检查；缺少覆盖不能证明避堵。",route.congestion_fraction,request.max_congestion_fraction,refs))
    elif request.prefer_avoid_congestion and route.mode in ("driving","drive_walk"):
        available = route.traffic_coverage is not None and route.traffic_coverage>=1-1e-6 and route.congestion_fraction is not None
        checks.append(check("congestion_reference","pass" if available else "unknown",
            "已取得路线拥堵参考，可用于候选比较。" if available else
            f"有效路况覆盖 {(route.traffic_coverage or 0):.0%}；可比较已覆盖路段，但未覆盖道路的拥堵未知，尚不能核实整条路线更避堵。",
            route.congestion_fraction,refs=refs,hard=False))
    if request.require_open and not route.poi:
        checks.append(check("opening_hours","unknown","没有经停地点，无法核实营业条件。"))
    if request.require_open and route.poi:
        hours = route.poi.opening_hours or ""
        if hours=="24/7":
            state,reason = "pass","OSM 营业标签为全天开放；标签不代表现场核验。"
        elif re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}",hours):
            # Arrival interval up to the actual leg ending at the POI.
            before = []
            for leg in route.legs:
                before.append(leg)
                if leg.destination.id==route.poi.id:
                    break
            partial = route.model_copy(update={"legs":before,"stop_s":0})
            lo,hi = time_bounds(partial)
            if route.mode=="transit":
                lo=hi=(route.poi_arrival_time-request.departure_time).total_seconds() if route.poi_arrival_time else None
            if lo is None:
                state,reason = "unknown","到店时段缺少时间证据。"
            else:
                a,b = hours.split("-")
                try:
                    ah,am = map(int,a.split(":")); bh,bm = map(int,b.split(":"))
                    earliest = request.departure_time+timedelta(seconds=lo)
                    latest = request.departure_time+timedelta(seconds=hi+route.stop_s)
                    opening = earliest.replace(hour=ah,minute=am,second=0,microsecond=0)
                    closing = earliest.replace(hour=bh,minute=bm,second=0,microsecond=0)
                    if closing<=opening:
                        state,reason = "unknown","跨夜营业标签尚不能可靠核实。"
                    elif opening<=earliest and latest<=closing:
                        state,reason = "pass","预计整个经停时段处于 OSM 标注的营业区间。"
                    elif earliest>=closing or latest<=opening:
                        state,reason = "fail","预计经停时段处于营业区间之外。"
                    else:
                        state,reason = "unknown","预计时段跨越营业边界。"
                except ValueError:
                    state,reason = "unknown","营业时间标签格式无效。"
        else:
            state,reason = "unknown","缺少可可靠解释的营业时间，未猜测复杂表达式。"
        checks.append(check("opening_hours",state,reason,hours,"open",route.poi.evidence_ids))
    if route.parking:
        p = route.parking
        fresh = True
        if evidence is not None:
            from .providers import now_utc
            fresh = bool(p.evidence_ids) and all(evidence.get(e) and evidence[e].valid_until and
                evidence[e].valid_until>=now_utc() for e in p.evidence_ids)
        state = "unknown" if p.available_lots is None or not fresh else "pass" if p.available_lots>0 else "fail"
        checks.append(check("parking",state,"仅核对查询时的车位，不保证到达时可用。",p.available_lots,">0 now",p.evidence_ids))
        checks.append(check("parking_access","pass" if p.entrance_confirmed else "unknown",
            "停车场入口与步行出口未现场核实；可在澄清中确认实际转换点。",refs=p.evidence_ids))
        if request.parking_name and request.parking_name.casefold() not in p.name.casefold():
            checks.append(check("named_parking","fail","停车点不符合用户指定名称。",p.name,request.parking_name,p.evidence_ids))
    if request.require_dry:
        checks.append(check("dry_weather","unknown","天气模型网格或区域预报不能证明全程实际不淋雨。",refs=route.weather.evidence_ids if route.weather else []))
    if request.weather_required or request.prefer_avoid_rain:
        available=bool(route.weather and route.weather.status=="available")
        checks.append(check("weather_reference","pass" if available else "unknown",
            route.weather.timing_basis if available else "请求了天气参考，但目前缺少有效预报。",
            refs=route.weather.evidence_ids if route.weather else [],hard=False))
    if request.prefer_avoid_rain:
        checks.append(check("rain_exposure","unknown",
            "天气预报可供比较，但未取得经停设施、停车点及楼宇入口的遮蔽证据，不能确认上下车和经停时能避雨。"
            if route.mode in ("driving","drive_walk") else
            "天气预报可供比较，但未取得步行及换乘通道的连续遮蔽证据，不能确认实际避雨条件。",
            refs=route.weather.evidence_ids if route.weather else [],hard=False))
    if request.prefer_low_cost:
        checks.append(check("cost","pass" if route.fare_sgd is not None else "unknown",
            route.fare_basis,route.fare_sgd,"完整交通费用参考",route.fare_evidence_ids,hard=False))
    if request.prefer_fastest:
        checks.append(check("speed_reference","pass" if route.total_s is not None else "unknown",
            "可比较查询到的服务预计时间；不证明全局最快。" if route.total_s is not None else
            "仅有基础行程时间，完整交通时间未知，不能确认最快。",hard=False))
    if request.multiple_stops:
        checks.append(check("multiple_stops","unknown","只支持一个业务经停点，需用户修改条件。"))
    route.checks = checks
    route.feasibility = "violated" if any(c.status=="fail" for c in checks if c.hard) else "unverified" if any(c.status=="unknown" for c in checks if c.hard) else "verified"
    return route
