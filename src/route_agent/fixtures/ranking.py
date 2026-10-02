DEFAULT_WEIGHTS = {"time": .7, "distance": .2, "uncertainty": .1, "congestion": 0}
CONGESTION_WEIGHTS = {"time": .5, "distance": .1, "uncertainty": .1, "congestion": .3}


def rank_routes(candidates, request, reference_time, reference_distance, *, personalized=True):
    weights = CONGESTION_WEIGHTS if personalized and request.prefer_avoid_congestion else DEFAULT_WEIGHTS
    for route in candidates:
        route.score = (weights["time"]*route.total_s/max(reference_time, 1)
                       + weights["distance"]*route.distance_m/max(reference_distance, 1)
                       + weights["uncertainty"]*route.unknown_fraction
                       + weights["congestion"]*route.congestion_fraction)
        if personalized and (request.poi_category or request.poi_name) and not request.poi_required and route.poi is None:
            route.score += .2  # explicit optional-stop preference, never a hard constraint
        if personalized and request.prefer_avoid_highways and route.highways:
            route.score += .2
        route.evidence["ranking_weights"] = weights
        route.evidence["normalization"] = {"time_s": reference_time, "distance_m": reference_distance}
    return sorted(candidates, key=lambda r: (r.score, r.total_s, r.id))


def explain(route):
    stop = f"经停 {route.poi.name}，停留 {route.stop_s/60:g} 分钟。" if route.poi else "直接前往终点。"
    message = (f"预计总时间 {route.total_s/60:.1f} 分钟，其中行驶 {route.driving_s/60:.1f} 分钟；"
               f"距离 {route.distance_m/1000:.2f} 公里。{stop}"
               f"交通数据覆盖 {route.traffic_coverage:.0%}。")
    message += f"已覆盖路段的拥堵长度占比 {route.congestion_fraction:.0%}。" if route.traffic_coverage else "暂无交通覆盖，拥堵程度无法判断。"
    if route.feasibility == "verified":
        message += "在当前路网、证据与估计模型下，全部硬约束通过。"
    else:
        missing = [c.constraint for c in route.checks if c.status != "pass"]
        message += "待核实项目：" + "、".join(missing) + "。"
    message += "按显示的偏好权重与指标排序；实际行驶、停车和步行接驳可能产生额外时间。"
    if route.evidence.get("synthetic"):
        message += "本结果使用合成演示路网和交通数据，不是真实导航建议。"
    return message
