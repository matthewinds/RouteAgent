"""Publish guards evaluate provider facts, never the model's claimed feasibility."""
import re
from datetime import timedelta
from .models import CheckResult
from .providers import distance
def check(name,state,reason,actual=None,required=None,refs=None):
    return CheckResult(constraint=name,status=state,reason=reason,actual=actual,required=required,evidence_ids=refs or [])

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
        route.total_s = None
        route.traffic_coverage = 0
        route.time_basis = "OSRM 基础服务估计，交通证据已过期；非实时总 ETA"
    refs = route.evidence_ids
    expected = ["driving"] if request.mode=="driving" else ["walking"] if request.mode=="walking" else ["driving","walking"]
    actual_modes = list(dict.fromkeys(x.mode for x in route.legs))
    checks = [check("mode","pass" if actual_modes==expected else "fail","核对实际分段交通方式。",actual_modes,expected,refs)]
    gaps = [distance(a.geometry["coordinates"][-1],b.geometry["coordinates"][0]) for a,b in zip(route.legs,route.legs[1:])]
    # Never silently draw a straight connector or add zero-time transfers.
    checks.append(check("continuity","unknown" if any(g>2 for g in gaps) else "pass",
        "API 分段入口存在未建模连接，需核实实际通行。" if any(g>2 for g in gaps) else "API 分段几何连续。",gaps,2,refs))
    if request.poi_required:
        ok = route.poi is not None and (not request.poi_category or route.poi.category==request.poi_category)
        if request.poi_name and route.poi:
            ok &= request.poi_name.casefold() in route.poi.name.casefold()
        checks.append(check("required_poi","pass" if ok else "fail","按真实 POI 身份与经停段检查。",route.poi.name if route.poi else None,request.poi_name or request.poi_category,refs))
    if request.avoid_highways:
        legs = [x for x in route.legs if x.mode=="driving"]
        state = "fail" if any(x.highways is True for x in legs) else "unknown" if any(x.highways is None for x in legs) else "pass"
        checks.append(check("avoid_highways",state,"使用 OSRM 避开高速选项的服务证据；未返回证据不能确认。",refs=refs))
    if request.max_walking_m is not None:
        checks.append(check("walking_distance","pass" if route.walking_m<=request.max_walking_m else "fail",
            "累计所有步行段的 API 距离。",route.walking_m,request.max_walking_m,refs))
    lower,upper = time_bounds(route)
    limits = []
    if request.max_duration_s is not None:
        limits.append(("duration",request.max_duration_s))
    if request.arrival_deadline:
        limits.append(("arrival_deadline",(request.arrival_deadline-request.departure_time).total_seconds()))
    for name,limit in limits:
        if route.stop_s+(route.parking_s or 0)>limit:
            state,reason = "fail","仅用户确认的停留和停车预留就已超出限制。"
        elif lower is None:
            state,reason = "unknown","缺少完整驾车交通时间区间或停车预留，不能核实总时间。"
        elif lower>limit:
            state,reason = "fail","服务时间估计的乐观值仍超出限制。"
        elif upper<=limit:
            state,reason = "pass","按 LTA 速度区间／OSRM 步行服务估计和用户预留满足；不保证实际准时。"
        else:
            state,reason = "unknown","时间估计区间跨越限制。"
        checks.append(check(name,state,reason,route.total_s,limit,refs))
    if request.max_congestion_fraction is not None:
        state = "unknown" if route.traffic_coverage is None or route.traffic_coverage<1-1e-6 or route.congestion_fraction is None else (
            "pass" if route.congestion_fraction<=request.max_congestion_fraction else "fail")
        checks.append(check("congestion",state,"仅按有效匹配的 LTA 路段检查；缺少覆盖不能证明避堵。",route.congestion_fraction,request.max_congestion_fraction,refs))
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
    if request.multiple_stops:
        checks.append(check("multiple_stops","unknown","只支持一个业务经停点，需用户修改条件。"))
    route.checks = checks
    route.feasibility = "violated" if any(c.status=="fail" for c in checks if c.hard) else "unverified" if any(c.status=="unknown" for c in checks if c.hard) else "verified"
    return route
