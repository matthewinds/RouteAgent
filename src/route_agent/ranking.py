"""Transparent comparison tools; the LLM chooses only among validated candidates."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .traffic import future_departure, past_departure

CHECK_LABELS = {"mode":"交通方式","required_poi":"必经地点","avoid_highways":"避开高速","duration":"总时间上限",
    "arrival_deadline":"到达时限","congestion":"拥堵上限","opening_hours":"营业时间","continuity":"分段连接",
    "walking_distance":"累计步行距离","parking":"当前停车位","parking_access":"停车转换入口",
    "named_parking":"指定停车点","dry_weather":"全程不淋雨","multiple_stops":"多个经停点",
    "cost":"交通费用","weather_reference":"天气参考","speed_reference":"完整预计时间",
    "congestion_reference":"避堵比较","rain_exposure":"避雨条件","transit_timing":"候车、换乘与经停时序","car_access":"车辆可用性",
    "lta_train_status":"LTA 地铁运行状态","lta_current_reference":"LTA 当前交通参考",
    **{"lta_bus_arrival_"+str(i):"LTA 第 "+str(i)+" 段公交到站参考" for i in range(1,21)}}

def historical_traffic(request, context=None):
    context = context or {}
    reference = context.get("retrieved_at") or context.get("evaluated_at")
    return context.get("applicability")=="past_departure" or past_departure(
        request.departure_time,datetime.fromisoformat(reference) if reference else None)

def place_label(place):
    if place.source=="OSM Overpass":
        return place.name+" · "+(place.address or f"{place.lat:.5f}, {place.lon:.5f}")
    return place.name

def diverse_references(routes, limit=3):
    """Show the best option for different stops before small path variations."""
    picked, remaining, seen = [], [], set()
    for route in routes:
        key=(route.mode,route.poi.id if route.poi else None,route.parking.id if route.parking else None)
        if key in seen:
            remaining.append(route)
        else:
            seen.add(key)
            picked.append(route)
    return (picked+remaining)[:limit]

def poi_search_summary(searches):
    if not searches:
        return None
    count=len({ident for search in searches for ident in search.get("place_ids",[])})
    if not any("place_ids" in search for search in searches):
        count=max(s["count"] for s in searches)
    return f"共进行了 {len(searches)} 次地点搜索，取得 {count} 个真实经停候选；系统继续按实际路线比较。搜索范围有限。"

def scheduled_traffic(request, context=None):
    context = context or {}
    reference = context.get("retrieved_at") or context.get("evaluated_at")
    return context.get("applicability")=="future_departure" or future_departure(
        request.departure_time,datetime.fromisoformat(reference) if reference else None)

def verification_summary(route):
    passed = sum(c.status=="pass" for c in route.checks)
    pending = [CHECK_LABELS.get(c.constraint,c.constraint) for c in route.checks if c.status=="unknown"]
    failed = [CHECK_LABELS.get(c.constraint,c.constraint) for c in route.checks if c.status=="fail"]
    pieces = [f"已通过 {passed} 项检查"]
    if pending:
        pieces.append(f"仍有 {len(pending)} 项缺少证据："+"、".join(pending))
    if failed:
        pieces.append("未满足："+"、".join(failed))
    return "；".join(pieces)+"。"

def base_arrival_note(route,request):
    if route.provider_total_s is None or request.departure_time is None:
        return None
    arrival = request.departure_time+timedelta(seconds=route.provider_total_s)
    note = "按基础估计约 "+arrival.astimezone(ZoneInfo("Asia/Singapore")).strftime("%m-%d %H:%M")+" 到达（新加坡时间）"
    if request.arrival_deadline:
        slack = (request.arrival_deadline-arrival).total_seconds()/60
        note += f"，距到达时限约有 {slack:.1f} 分钟余量" if slack>=0 else f"，超过到达时限约 {-slack:.1f} 分钟"
    return note+"；未计入未知交通延误及未建模的停车、进出楼宇时间。"

def estimated_arrival_note(route,request):
    if route.estimated_total_s is None or request.departure_time is None:
        return None
    arrival=request.departure_time+timedelta(seconds=route.estimated_total_s)
    note="按部分路况估计约 "+arrival.astimezone(ZoneInfo("Asia/Singapore")).strftime("%m-%d %H:%M")+" 到达（新加坡时间）"
    if request.arrival_deadline:
        slack=(request.arrival_deadline-arrival).total_seconds()/60
        note+=f"，估计余量 {slack:.1f} 分钟" if slack>=0 else f"，估计超时 {-slack:.1f} 分钟"
    return note+"；未覆盖道路的实际延误未知，到达时限尚未核实。"

def comparison_time(route):
    return route.total_s if route.total_s is not None else route.estimated_total_s if route.estimated_total_s is not None else route.provider_total_s

def congestion_bounds(route):
    if route.congestion_fraction is None or route.traffic_coverage is None:
        return 0,1
    observed=route.congestion_fraction*route.traffic_coverage
    return observed,min(1,observed+1-route.traffic_coverage)

def exposure_reference(route):
    waiting=max(0,(route.provider_total_s or 0)-sum(l.provider_duration_s for l in route.legs)-route.stop_s-(route.parking_s or 0)) if route.mode=="transit" else 0
    return {"walking_s":route.walking_s,"waiting_s":waiting,
        "potential_outdoor_s":route.walking_s+waiting+route.stop_s+(route.parking_s or 0),
        "basis":"按服务步行、候车换乘与停留时间比较潜在暴露；遮蔽未知的等待和停留保守计入，不代表实际淋雨时长"}

def itinerary_label(route):
    labels={"walking":"步行","driving":"自驾","bus":"公交","rail":"地铁／轨道"}
    return " → ".join(labels[l.mode]+(" "+l.service_name if l.service_name else "") for l in route.legs)

def rank_routes(routes,request):
    times = [comparison_time(r) for r in routes if comparison_time(r) is not None]
    ref_time = min(times) if times else 1
    ref_distance = min((r.distance_m for r in routes),default=1)
    weights = {"time":.5,"distance":.1,"congestion":.3,"uncertainty":.1} if request.prefer_avoid_congestion else {"time":.7,"distance":.2,"congestion":0,"uncertainty":.1}
    if request.prefer_fastest and not (request.prefer_avoid_congestion or request.prefer_avoid_rain or request.prefer_low_cost):
        weights={"time":1,"distance":0,"congestion":0,"uncertainty":0}
    if request.prefer_avoid_rain:
        weights = {k:v*.55 for k,v in weights.items()}
        weights["rain"] = .45
    if request.prefer_low_cost:
        weights = {k:v*.5 for k,v in weights.items()}
        weights["cost"] = .5
    fares=[r.fare_sgd for r in routes if r.fare_sgd is not None]
    fare_scale=max(1,max(fares,default=1))
    exposure_scale=max(1,max((exposure_reference(r)["potential_outdoor_s"] for r in routes),default=1))
    for r in routes:
        uncertainty = sum(c.status=="unknown" for c in r.checks)/max(1,len(r.checks))
        if r.mode in ("driving","drive_walk"):
            uncertainty = max(uncertainty,1-(r.traffic_coverage or 0))
        rain = r.weather.rain_fraction if r.weather and r.weather.rain_fraction is not None else 1
        rain *= exposure_reference(r)["potential_outdoor_s"]/exposure_scale
        time_value = comparison_time(r)
        # A quiet observed 5% cannot outrank a comparable observed 90% by
        # treating all unobserved roads as uncongested.
        congestion_risk=congestion_bounds(r)[1]
        r.score = weights["time"]*(time_value/max(1,ref_time) if time_value is not None else 2)+weights["distance"]*r.distance_m/max(1,ref_distance)+weights["uncertainty"]*uncertainty+weights["congestion"]*congestion_risk+weights.get("rain",0)*rain
        r.score += weights.get("cost",0)*(r.fare_sgd/fare_scale if r.fare_sgd is not None else 2)
        if (request.poi_category or request.poi_name) and not request.poi_required and r.poi is None:
            r.score += .2
        if request.prefer_avoid_highways and any(x.highways is not False for x in r.legs if x.mode=="driving"):
            r.score += .2
    return sorted(routes,key=lambda r:(r.score,r.id)),weights

REASON_LABELS = {"time":"时间指标","distance":"距离指标","cost":"已取得的费用参考","traffic":"LTA 交通覆盖与拥堵指标",
    "weather":"真实天气预报参考","walking":"累计步行距离","poi":"真实经停地点","parking":"当前停车信息"}

def time_notes(route,traffic_context=None,request=None):
    notes = []
    if route.parking and route.parking_s is None:
        notes.append("停车及转换预留时间尚未确认，基础总时长也无法完整汇总。")
    if route.mode in ("driving","drive_walk") and route.total_s is None:
        context = traffic_context or {}
        if request and historical_traffic(request,context):
            notes.append("本次填写的出发时间已早于路况有效窗口，当前实时路况不能还原当时的交通。展示基础路线参考，到达时限和避堵效果尚未核实；如计划之后出发，请修改出发时间。")
            return notes
        if request and scheduled_traffic(request,context):
            notes.append("预约出发方案：当前路况不能预测该时段。基础路线可供提前规划，到达时限与避堵效果需临近出发时更新交通数据；当前并非路线规划失败。")
            return notes
        if context.get("missing"):
            notes.append("交通查询未完成："+context["missing"])
        incomplete=[item for item in context.get("collection",{}).values() if not item.get("complete")]
        if incomplete:
            notes.append("部分交通数据未读取完整，已保留成功结果；采集详情见工具记录。")
        notes.append(f"有效交通证据覆盖 {route.traffic_coverage:.0%} 的驾车距离，尚不足以核实完整交通修正时间。"
            if route.traffic_coverage is not None else "尚无有效驾车交通时间证据。")
    notes.extend(c.reason for c in route.checks if c.constraint in ("duration","arrival_deadline") and c.status=="unknown")
    return list(dict.fromkeys(notes))

def explain(route,include_unknown=True):
    minutes = route.total_s/60 if route.total_s is not None else None
    pieces = [f"距离 {route.distance_m/1000:.2f} 公里。"]
    if minutes is not None:
        pieces.append(f"预计总时间 {minutes:.1f} 分钟（{route.time_basis}）。")
    elif route.provider_total_s is not None:
        pieces.append(f"基础行程估计 {route.provider_total_s/60:.1f} 分钟，未计入未知交通延误。")
    else:
        pieces.append("总时间尚未核实。")
    if route.total_s is None and route.estimated_total_s is not None:
        pieces.append(f"结合已覆盖路段的部分路况估计 {route.estimated_total_s/60:.1f} 分钟；未覆盖部分沿用基础估计。")
    if route.poi:
        pieces.append(f"经停 {place_label(route.poi)}，停留 {route.stop_s/60:g} 分钟。")
    if route.parking:
        pieces.append(f"停车转换点：{route.parking.name}；当前车位 {route.parking.available_lots if route.parking.available_lots is not None else '未知'}，不保证到达时可用。")
    if route.mode!="driving":
        pieces.append(f"累计步行 {route.walking_m:.0f} 米，步行服务估计 {route.walking_s/60:.1f} 分钟。")
    if route.mode=="transit":
        pieces.append("分段行程："+itinerary_label(route)+"。")
        pieces.append(f"候车及换乘等待约 {exposure_reference(route)['waiting_s']/60:.1f} 分钟，已计入总时间。")
    if include_unknown and route.traffic_coverage is not None:
        pieces.append(f"LTA 路段覆盖 {route.traffic_coverage:.0%}。")
    if route.fare_sgd is not None:
        pieces.append(f"交通费用参考 S${route.fare_sgd:.2f}（{route.fare_basis}）。")
    if route.reasons:
        pieces.append("DeepSeek 依据："+ "、".join(REASON_LABELS[x] for x in route.reasons))
    unknown = [CHECK_LABELS.get(c.constraint,c.constraint) for c in route.checks if c.status=="unknown"]
    if unknown and include_unknown:
        pieces.append("待核实："+ "、".join(unknown)+"。")
    pieces.append("API 预计时间和预报不保证实际准时、无雨或有停车位。")
    return " ".join(pieces)
