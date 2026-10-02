"""Deterministic checks. Unknown hard evidence is never a verified pass."""
from datetime import timedelta
from .models import CheckResult


def check(constraint, state, actual, required, reason, evidence=""):
    return CheckResult(constraint=constraint, status=state, actual=actual, required=required,
                       reason=reason, evidence=evidence)


def opening_check(candidate, request):
    if not request.require_open or not candidate.poi:
        return None
    hours = candidate.poi.opening_hours
    arrival = request.departure_time + timedelta(seconds=candidate.poi_arrival_s or 0)
    finish = arrival + timedelta(seconds=candidate.stop_s)
    if hours == "24/7":
        state, reason = "pass", "营业信息标注全天开放。"
    elif hours == "closed":
        state, reason = "fail", "此快照标注该地点关闭。"
    else:
        # Only a simple daily interval is supported. OSM's full expression syntax
        # is not guessed: weekdays, exceptions, holidays return unknown.
        import re
        pattern = re.fullmatch(r"(\d{2}):(\d{2})-(\d{2}):(\d{2})", hours or "")
        earliest = request.departure_time + timedelta(seconds=candidate.evidence.get("lower_poi_arrival_s",candidate.poi_arrival_s or 0))
        latest_seconds = candidate.evidence.get("upper_poi_arrival_s")
        latest_finish = request.departure_time + timedelta(seconds=latest_seconds+candidate.stop_s) if latest_seconds is not None else None
        if candidate.traffic_coverage < 1-1e-8 or latest_finish is None:
            state, reason = "unknown", "到店路段缺少交通证据，无法可靠核实经停时段。"
        elif pattern:
            start_min, end_min = int(pattern[1])*60+int(pattern[2]), int(pattern[3])*60+int(pattern[4])
            if any(int(pattern[i]) > 59 for i in (2, 4)) or any(int(pattern[i]) > 23 for i in (1, 3)) or end_min <= start_min:
                state, reason = "unknown", "营业表达式无法可靠解释。"
            else:
                start = earliest.replace(hour=int(pattern[1]),minute=int(pattern[2]),second=0,microsecond=0)
                end = earliest.replace(hour=int(pattern[3]),minute=int(pattern[4]),second=0,microsecond=0)
                fits = earliest.date() == latest_finish.date() and start <= earliest and latest_finish <= end
                definitely_outside = earliest >= end or latest_finish <= start
                state, reason = ("pass", "速度区间下的整个经停时段处于营业时间。") if fits else (
                    ("fail", "预计经停时段超出营业时间。") if definitely_outside else
                    ("unknown", "可能的到店时段跨越营业边界，不能确认开放。"))
        else:
            state, reason = "unknown", "缺少可可靠解释的营业时间，不能确认到店时开放。"
    return check("opening_hours", state, hours, "open for stop", reason, candidate.poi.source)


def verify_route(candidate, request):
    checks = []
    checks.append(check("mode", "pass" if request.mode == "driving" else "fail", request.mode, "driving", "按驾车路网计算。"))
    if request.poi_required:
        matches = candidate.poi is not None and (not request.poi_name or
            candidate.poi.id == request.resolved_poi_id or candidate.poi.name.casefold() == request.poi_name.casefold())
        if candidate.poi and request.poi_category and not request.poi_name:
            matches &= candidate.poi.category == request.poi_category
        checks.append(check("required_poi", "pass" if matches else "fail", candidate.poi.name if candidate.poi else None,
                            request.poi_name or request.poi_category, "已包含所需道路经停点。" if matches else "缺少要求的经停点。"))
    if request.avoid_highways:
        checks.append(check("avoid_highways", "fail" if candidate.highways else "pass", candidate.highways, False, "检查了完整路径的高速道路标签。"))
    checks.append(check("closed_roads", "fail" if candidate.closed else "pass", candidate.closed, False,
                        "未经过快照中明确标记的封闭道路；不代表所有未知事故均已排除。"))
    limits = []
    if request.max_duration_s is not None:
        limits.append(("duration", request.max_duration_s))
    if request.arrival_deadline is not None:
        limits.append(("arrival_deadline", (request.arrival_deadline-request.departure_time).total_seconds()))
    for name, limit in limits:
        lower = candidate.evidence["lower_total_s"]
        upper = candidate.evidence["upper_total_s"]
        if candidate.stop_s > limit:
            state, reason = "fail", "仅经停时间就超过总时间限制，无需交通数据即可确定冲突。"
        elif candidate.traffic_coverage < 1-1e-8:
            # Free-flow time on uncovered edges is not a proven lower bound.
            state = "unknown"
            reason = "部分路段缺少有效交通数据，总时间不能完全核实。"
        elif lower > limit:
            state, reason = "fail", "即使按速度区间乐观估计，总时间仍超过限制。"
        elif upper is not None and upper <= limit:
            state, reason = "pass", "按当前速度区间估计，行驶与停留时间满足限制。"
        else:
            state, reason = "unknown", "速度区间对应时间跨越到达限制，不能确认准时。"
        checks.append(check(name, state, round(candidate.total_s, 2), limit, reason,
                            str(candidate.evidence.get("traffic", {}).get("observed_at", ""))))
    if request.max_congestion_fraction is not None:
        state = "unknown" if candidate.traffic_coverage < 1-1e-8 else (
            "pass" if candidate.congestion_fraction <= request.max_congestion_fraction else "fail")
        checks.append(check("congestion", state, candidate.congestion_fraction, request.max_congestion_fraction,
                            "拥堵检查基于已匹配且有效的路段。"))
    opening = opening_check(candidate, request)
    if opening:
        checks.append(opening)
    if request.weather_required or request.max_walking_m is not None or request.multiple_stops:
        checks.append(check("unsupported_constraint", "unknown", None, "weather/walking", "首版不支持该项验证。"))
    candidate.checks = checks
    candidate.feasibility = "violated" if any(c.status == "fail" for c in checks) else (
        "unverified" if any(c.status == "unknown" for c in checks) else "verified")
    return candidate
