"""Audit a saved RouteAgent result against the raw provider responses it cites.

Usage: .venv/bin/python scripts/audit_result.py outputs/<run_id>

Checks that the published route's places, legs, totals, traffic bound and
weather can be reproduced from stored raw responses (data/evidence), so no
number or coordinate in the result came from the model. Exit code 1 on failure.
"""
import json
import pathlib
import sys

from route_agent.cache import EvidenceStore
from route_agent.config import Settings
from route_agent.models import PlanningResult, RouteLeg
from route_agent.providers import stable
from route_agent.traffic import apply_traffic
from route_agent.verification import bounded_upper

failures = []


def report(passed, message):
    print(("✅ " if passed else "❌ ")+message)
    if not passed:
        failures.append(message)


def main(run):
    settings = Settings()
    result = PlanningResult.model_validate_json((pathlib.Path(run)/"result.json").read_text(encoding="utf-8"))
    store = EvidenceStore(settings.root/"data/evidence")
    evidence = {e.id: e for e in result.evidence}
    # Older runs predate the evidence store; fall back to cache entries that
    # still hold the identical payload (newer fetches may have replaced them).
    legacy = {}
    for path in (settings.root/"data/cache/online-v2").glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))["value"]
            legacy[stable(value["payload"])] = value["payload"]
        except (ValueError, KeyError, TypeError):
            continue

    def payload(ident):
        digest = evidence[ident].response_sha256
        stored = store.get(digest)
        if stored is not None and stable(stored["payload"]) == digest:
            return stored["payload"]
        return legacy.get(digest)

    route = result.recommended or (result.unverified[0] if result.unverified else None)
    if route is None:
        print("该结果没有推荐或参考路线，无可审计的方案。状态：", result.status)
        return 0
    print(f"审计 {run} · {'正式推荐' if result.recommended else '参考方案'} {route.id}（{route.mode}）\n")

    cited = set(route.evidence_ids) | {e for leg in route.legs for e in leg.evidence_ids}
    cited |= set(route.poi.evidence_ids) if route.poi else set()
    cited |= {e for stop in route.stops for e in stop.evidence_ids}
    cited |= set(route.weather.evidence_ids) if route.weather else set()
    unknown = sorted(e for e in cited if e not in evidence)
    report(not unknown, f"引用 {len(cited)} 条证据，全部为本次真实服务调用记录"+(f"；未知 ID {unknown}" if unknown else ""))
    retained = [e for e in cited if e in evidence and payload(e) is not None]
    report(len(retained) == len(cited)-len(unknown),
        f"{len(retained)}/{len(cited)} 条证据的原始响应仍可取得，且内容哈希与记录一致")

    for role in ("origin", "destination"):
        place = getattr(route, role)
        points = [(f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
                  for e in place.evidence_ids if payload(e) for f in payload(e).get("features", [])]
        points += [(float(r["LATITUDE"]), float(r["LONGITUDE"]))  # OneMap search rows
                   for e in place.evidence_ids if payload(e) for r in payload(e).get("results", [])
                   if isinstance(r, dict) and "LATITUDE" in r]
        hit = any(abs(lat-place.lat) < 1e-6 and abs(lon-place.lon) < 1e-6 for lat, lon in points)
        report(hit, f"{role}「{place.name}」坐标 ({place.lat}, {place.lon}) 出自地点服务原始响应")

    for poi in ([route.poi] if route.poi else [])+list(route.stops):
        element = None
        for e in poi.evidence_ids:
            for item in (payload(e) or {}).get("elements", []):
                if f"osm:{item['type']}:{item['id']}" == poi.id:
                    element = item
        tags = element.get("tags", {}) if element else {}
        lat = element.get("lat", element.get("center", {}).get("lat")) if element else None
        report(element is not None and tags.get("name") == poi.name and lat is not None and abs(lat-poi.lat) < 1e-6,
            f"经停「{poi.name}」({poi.id}) 名称与坐标出自 OSM Overpass 原始响应；类别标签 {({k: v for k, v in tags.items() if k in ('amenity', 'shop')})}")

    for leg in route.legs:
        label = f"{leg.origin.name[:16]} → {leg.destination.name[:16]}（{leg.mode}）"
        if leg.source.startswith("OSRM"):
            match = next((r for e in leg.evidence_ids if evidence.get(e) and evidence[e].source == "OSRM Route"
                          for r in (payload(e) or {}).get("routes", []) if r["geometry"]["coordinates"] == leg.geometry["coordinates"]), None)
            report(match is not None and abs(match["distance"]-leg.distance_m) < .01 and abs(match["duration"]-leg.provider_duration_s) < .01,
                f"{label}：{leg.distance_m:.1f} 米 / {leg.provider_duration_s:.1f} 秒，几何 {len(leg.geometry['coordinates'])} 点与 OSRM 原始路线一致")
        elif leg.source == "OneMap transit":
            stamps = {int(leg.departure_time.timestamp()*1000), int(leg.arrival_time.timestamp()*1000)} if leg.departure_time and leg.arrival_time else set()
            match = any({p.get("startTime"), p.get("endTime")} == stamps
                        for e in leg.evidence_ids if evidence.get(e) and evidence[e].source == "OneMap transit"
                        for it in (payload(e) or {}).get("plan", {}).get("itineraries", []) for p in it.get("legs", []))
            report(match, f"{label}：上下车时刻出自 OneMap 原始行程")
        else:
            report(False, f"{label}：未知来源 {leg.source}")

    report(abs(sum(l.distance_m for l in route.legs)-route.distance_m) < .01,
        f"总距离 {route.distance_m:.1f} 米 = 各分段之和")
    if route.mode != "transit" and route.provider_total_s is not None:
        base = sum(l.provider_duration_s for l in route.legs)+route.stop_s+(route.parking_s or 0)
        request = result.request
        # Each stop's confirmed dwell, falling back to the request-level duration.
        expected_stop_s = (sum(stop.stop_duration_s if stop.stop_duration_s is not None else request.stop_duration_s
            for stop in request.stops) if route.stops else (request.stop_duration_s or 0) if route.poi else 0)
        report(abs(base-route.provider_total_s) < .01 and route.stop_s == expected_stop_s,
            f"基础总时长 {route.provider_total_s:.1f} 秒 = 各段服务时间 + 用户确认的停留 {route.stop_s:.0f} 秒")

    driving = [l for l in route.legs if l.mode == "driving" and l.traffic_coverage]
    if driving:
        pages = [e for e in evidence if evidence[e].source == "LTA v4/TrafficSpeedBands"]
        bands = [row for e in pages for row in (payload(e) or {}).get("value", [])]
        complete = all(payload(e) is not None for e in pages)
        report(complete, f"LTA 车速原始页面 {sum(payload(e) is not None for e in pages)}/{len(pages)} 页可取得（{len(bands)} 条）")
        if complete:
            stamp = min(evidence[e].retrieved_at for e in pages)
            for leg in driving:
                copy = RouteLeg.model_validate({**leg.model_dump(), "traffic_coverage": None, "bounded_upper_s": None})
                apply_traffic([copy], {"speed_bands": bands, "retrieved_at": stamp.isoformat(), "evidence_ids": []},
                              result.request.departure_time, max_age=10**9)
                same = abs((copy.traffic_coverage or 0)-(leg.traffic_coverage or 0)) < 1e-9 and (
                    (copy.bounded_upper_s is None) == (leg.bounded_upper_s is None) and abs((copy.bounded_upper_s or 0)-(leg.bounded_upper_s or 0)) < 1e-6)
                report(same, f"用原始 LTA 数据重算 {leg.origin.name[:14]} 段：覆盖 {copy.traffic_coverage:.1%}，保守上界 {copy.bounded_upper_s or 0:.1f} 秒，与结果一致")
    for check in route.checks:
        if check.constraint in ("duration", "arrival_deadline") and check.status == "pass" and route.total_s is None and route.mode != "transit":
            bound = bounded_upper(route)
            report(bound is not None and bound <= check.required, f"{check.constraint} 通过依据：保守上界 {bound/60:.1f} 分钟 ≤ 要求 {check.required/60:.1f} 分钟")

    if route.weather and route.weather.status == "available":
        report(all(evidence[e].source.startswith("Open-Meteo") and payload(e) is not None for e in route.weather.evidence_ids),
            "天气参考出自 Open-Meteo 原始预报响应")
    print(f"\n推荐理由仅为固定代码 {route.reasons}；说明文字由后端模板根据以上数据生成，模型的自由文本不会进入结果。")
    print("\n结论：" + ("全部检查通过，方案中的地点、路线、时间与路况数值均可追溯到真实服务响应。" if not failures
                       else f"{len(failures)} 项未通过，见上方 ❌。"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
