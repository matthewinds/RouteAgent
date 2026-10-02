"""Transparent comparison tools; the LLM chooses only among validated candidates."""
def rank_routes(routes,request):
    times = [r.total_s or r.provider_total_s for r in routes if r.total_s is not None or r.provider_total_s is not None]
    ref_time = min(times) if times else 1
    ref_distance = min((r.distance_m for r in routes),default=1)
    weights = {"time":.5,"distance":.1,"congestion":.3,"uncertainty":.1} if request.prefer_avoid_congestion else {"time":.7,"distance":.2,"congestion":0,"uncertainty":.1}
    if request.prefer_avoid_rain:
        weights = {k:v*.8 for k,v in weights.items()}
        weights["rain"] = .2
    for r in routes:
        uncertainty = sum(c.status=="unknown" for c in r.checks)/max(1,len(r.checks))
        if r.mode!="walking":
            uncertainty = max(uncertainty,1-(r.traffic_coverage or 0))
        rain = r.weather.rain_fraction if r.weather and r.weather.rain_fraction is not None else 1
        time_value = r.total_s if r.total_s is not None else r.provider_total_s
        r.score = weights["time"]*(time_value/max(1,ref_time) if time_value is not None else 2)+weights["distance"]*r.distance_m/max(1,ref_distance)+weights["uncertainty"]*uncertainty+weights["congestion"]*(r.congestion_fraction if r.congestion_fraction is not None else 1)+weights.get("rain",0)*rain
        if (request.poi_category or request.poi_name) and not request.poi_required and r.poi is None:
            r.score += .2
        if request.prefer_avoid_highways and any(x.highways is not False for x in r.legs if x.mode=="driving"):
            r.score += .2
    return sorted(routes,key=lambda r:(r.score,r.id)),weights

REASON_LABELS = {"time":"时间指标","distance":"距离指标","traffic":"LTA 交通覆盖与拥堵指标",
    "weather":"真实天气预报参考","walking":"累计步行距离","poi":"真实经停地点","parking":"当前停车信息"}

def time_notes(route,traffic_context=None):
    notes = []
    if route.parking and route.parking_s is None:
        notes.append("停车及转换预留时间尚未确认，基础总时长也无法完整汇总。")
    if route.mode!="walking" and route.total_s is None:
        context = traffic_context or {}
        if context.get("missing"):
            notes.append("交通查询未完成："+context["missing"])
        for item in context.get("collection",{}).values():
            if not item.get("complete") and item.get("reason"):
                notes.append("交通数据未完整读取："+item["reason"])
        notes.append(f"有效交通证据覆盖 {route.traffic_coverage:.0%} 的驾车距离，尚不足以核实完整交通修正时间。"
            if route.traffic_coverage is not None else "尚无有效驾车交通时间证据。")
    notes.extend(c.reason for c in route.checks if c.constraint in ("duration","arrival_deadline") and c.status=="unknown")
    return list(dict.fromkeys(notes))

def explain(route):
    minutes = route.total_s/60 if route.total_s is not None else None
    pieces = [f"距离 {route.distance_m/1000:.2f} 公里。"]
    if minutes is not None:
        pieces.append(f"预计总时间 {minutes:.1f} 分钟（{route.time_basis}）。")
    elif route.provider_total_s is not None:
        pieces.append(f"OSRM 基础行程估计 {route.provider_total_s/60:.1f} 分钟，非完整实时 ETA；驾车交通证据不足。")
    else:
        pieces.append("总时间尚未核实。")
    if route.poi:
        pieces.append(f"经停 {route.poi.name}，停留 {route.stop_s/60:g} 分钟。")
    if route.parking:
        pieces.append(f"停车转换点：{route.parking.name}；当前车位 {route.parking.available_lots if route.parking.available_lots is not None else '未知'}，不保证到达时可用。")
    if route.mode!="driving":
        pieces.append(f"累计步行 {route.walking_m:.0f} 米，OSRM 步行服务估计 {route.walking_s/60:.1f} 分钟。")
    if route.traffic_coverage is not None:
        pieces.append(f"LTA 路段覆盖 {route.traffic_coverage:.0%}。")
    if route.reasons:
        pieces.append("DeepSeek 依据："+ "、".join(REASON_LABELS[x] for x in route.reasons))
    unknown = [c.constraint for c in route.checks if c.status=="unknown"]
    if unknown:
        pieces.append("待核实："+ "、".join(unknown)+"。")
    pieces.append("API 预计时间和预报不保证实际准时、无雨或有停车位。")
    return " ".join(pieces)
