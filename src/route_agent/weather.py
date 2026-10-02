"""Match provider forecasts to bounded real route samples, never invent dry streets."""
from datetime import datetime, timedelta
from .models import WeatherContext
from .providers import distance, now_utc

def route_points(route):
    points = []
    for leg in route.legs:
        coords = leg.geometry["coordinates"]
        points.extend([coords[0][:2],coords[len(coords)//2][:2],coords[-1][:2]])
    return list(dict.fromkeys(tuple(p) for p in points))

def weather_context(route, request, payload, evidence):
    if not payload:
        return WeatherContext()
    if payload.get("provider")!="open_meteo":
        return nea_context(route,request,payload)
    refs = payload["evidence_ids"]
    unknown = WeatherContext(evidence_ids=refs)
    if any(not evidence.get(e) or not evidence[e].valid_until or evidence[e].valid_until<now_utc() for e in refs):
        return unknown
    duration = route.total_s
    if duration is None:
        # No fabricated timing for traffic not covered by LTA.
        return unknown
    start = request.departure_time
    end = start+timedelta(seconds=duration)
    samples = []
    probabilities = []
    complete = True
    used = set()
    for coordinate in route_points(route):
        candidates = payload.get("points",[])
        if not candidates:
            complete = False
            continue
        point = min(candidates,key=lambda p:distance(coordinate,p["requested_coordinate"]))
        # This is a proximity tolerance between requested samples, NOT grid resolution.
        if distance(coordinate,point["requested_coordinate"])>1000:
            complete = False
            continue
        key = tuple(point["requested_coordinate"])
        if key in used:
            continue
        used.add(key)
        hours = sorted((h for h in point["hours"] if datetime.fromisoformat(h["end"])>start and
            (datetime.fromisoformat(h["start"])<end if end>start else datetime.fromisoformat(h["start"])<=start)),key=lambda h:h["start"])
        covered_until = start
        for hour in hours:
            a,b = datetime.fromisoformat(hour["start"]),datetime.fromisoformat(hour["end"])
            if a>covered_until or hour["precipitation_probability"] is None:
                complete = False
            covered_until = max(covered_until,b)
            if hour["precipitation_probability"] is not None:
                probabilities.append(hour["precipitation_probability"]/100)
            samples.append({"采样经纬度":str(list(key)),"模型网格经纬度":str(point["grid_coordinate"]),
                "降水时段开始":a.isoformat(),"降水时段结束":b.isoformat(),
                "降水概率（%）":hour["precipitation_probability"],"降水量（mm）":hour["precipitation_mm"],
                "天气代码（时段结束时刻）":hour["weather_code"]})
        if covered_until<end or not hours:
            complete = False
    complete = complete and bool(samples) and bool(probabilities)
    return WeatherContext(status="available" if complete else "unknown",valid_start=start,valid_end=end,
        samples=samples,rain_fraction=max(probabilities) if complete else None,evidence_ids=refs)

def nea_context(route,request,payload):
    refs = payload["evidence_ids"]
    base = dict(source="NEA / data.gov.sg",evidence_ids=refs,
        mapping_basis="附近官方区域标签点，仅为区域预报参考，非路段观测。",
        risk_basis="比较值为采样区域中含雨预报的比例；不是整程淋雨概率。")
    data = payload["data"]
    valid = [x for x in data["items"] if x.get("valid_period") and
        datetime.fromisoformat(x["valid_period"]["start"])<=request.departure_time<datetime.fromisoformat(x["valid_period"]["end"])]
    if not valid:
        return WeatherContext(**base)
    item = valid[-1]
    start,end = [datetime.fromisoformat(item["valid_period"][k]) for k in ("start","end")]
    if route.total_s is None or request.departure_time+timedelta(seconds=route.total_s)>end:
        return WeatherContext(valid_start=start,valid_end=end,**base)
    metadata = data.get("area_metadata") or data.get("areaMetadata") or []
    forecasts = {x["area"]:x["forecast"] for x in item["forecasts"]}
    areas = {}
    for coord in route_points(route):
        if not metadata:
            break
        area = min(metadata,key=lambda x:distance(coord,[x.get("label_location",x.get("labelLocation",{}))["longitude"],
            x.get("label_location",x.get("labelLocation",{}))["latitude"]]))
        name = area["name"]
        areas[name] = {"area":name,"forecast":forecasts.get(name,"unknown")}
    unknown = any(x["forecast"]=="unknown" for x in areas.values()) or not areas
    rain = sum(any(word in x["forecast"].casefold() for word in ("rain","showers","thunder")) for x in areas.values())/len(areas) if areas and not unknown else None
    return WeatherContext(status="unknown" if unknown else "available",valid_start=start,valid_end=end,
        areas=list(areas.values()),rain_fraction=rain,**base)
