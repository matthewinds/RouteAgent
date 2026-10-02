"""Real OSRM routes, ORS geocoding, OSM, LTA and weather. No routing fallback."""
import hashlib
import json
import math
import re
import time
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
import httpx
from .cache import JsonCache
from .models import Evidence, Place, RouteLeg
from .tools import ToolFailure

def now_utc():
    return datetime.now(timezone.utc)

def stable(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def distance(a, b):
    la, lo, lb, lob = map(math.radians, [a[1], a[0], b[1], b[0]])
    h = math.sin((lb-la)/2)**2 + math.cos(la)*math.cos(lb)*math.sin((lob-lo)/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))

def singapore(lon, lat):
    return 103.5 <= lon <= 104.15 and 1.15 <= lat <= 1.5

def location_query(query):
    """Use an explicitly supplied Latin alias; never translate or invent a name."""
    text = unicodedata.normalize("NFKC", query).strip()
    if re.search(r"[\u3400-\u9fff]", text):
        aliases = re.findall(r"\(([^()]+)\)", text)
        for alias in aliases:
            if re.search(r"[A-Za-z]", alias) and not re.search(r"[\u3400-\u9fff]", alias):
                return alias.strip()
    return text

def location_name_matches(query, *names):
    # Complete phrase matching prevents a shared word (e.g. "Bay") from
    # turning a different landmark into a selectable destination.
    def words(value):
        return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))
    needle = words(query)
    return bool(needle) and any(" "+needle+" " in " "+words(name)+" " for name in names if isinstance(name,str))

class Providers:
    # Shared by all page workers: public OSRM endpoints allow at most 1 request/s.
    _routing_lock = threading.Lock()
    _routing_last_request = 0.0

    def __init__(self, settings, budget):
        self.settings, self.budget = settings, budget
        self.cache = JsonCache(settings.root / "data/cache/online-v2")
        self.evidence = {}
        self.call_id = "initial"
        self.cache_hits = 0
        self.last_evidence = []
        self.lta_status = {}
    def key(self, name):
        key = self.settings.credential(name)
        if not key:
            raise ToolFailure("请在 .env 中配置 " + name + "。", "missing_credential")
        return key
    def send_request(self, client, method, url, *, paced=False, **kwargs):
        if not paced:
            self.budget.http()
            return client.request(method,url,**kwargs)
        with Providers._routing_lock:
            wait = max(0,1.05-(time.monotonic()-Providers._routing_last_request))
            if wait>=self.budget.remaining():
                raise ToolFailure("等待 OSRM 请求间隔时已达到预算。","budget_exhausted")
            if wait:
                time.sleep(wait)
            self.budget.http()
            try:
                return client.request(method,url,**kwargs)
            finally:
                Providers._routing_last_request = time.monotonic()

    def request(self, method, url, source, *, params=None, body=None, data=None, headers=None, ttl=0, paced=False):
        args = {"method":method,"url":url,"params":params,"body":body,"data":data}
        cached = self.cache.get(source, args, ttl) if ttl else None
        if cached:
            payload, stamp = cached["payload"], datetime.fromisoformat(cached["retrieved_at"])
            self.cache_hits += 1
        else:
            payload = None
            for attempt in range(3):
                try:
                    with httpx.Client(timeout=max(.1, min(20, self.budget.remaining())), trust_env=False,
                                      follow_redirects=False) as client:
                        reply = self.send_request(client,method,url,paced=paced,params=params,json=body,data=data,headers=headers)
                    if reply.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        self.budget.check()
                        time.sleep(min(2**attempt, max(0, self.budget.remaining())))
                        continue
                    if reply.status_code in (401, 403):
                        raise ToolFailure(source+" 拒绝访问，请检查 Key、服务权限或配额。", "authentication_error")
                    if reply.status_code == 429:
                        raise ToolFailure(source+" 配额已用尽，请稍后重试。", "rate_limited")
                    if reply.status_code >= 400:
                        if source=="OSRM Route" and reply.status_code==400:
                            code = reply.json().get("code")
                            if code in ("NoRoute","NoSegment"):
                                raise ToolFailure("OSRM 当前路网未找到可行路线或地点入口。","no_route")
                            if code in ("InvalidValue","InvalidOptions") and params and params.get("exclude"):
                                raise ToolFailure("此 OSRM 实例不支持避高速排除选项；没有改用普通路线。","unsupported_feature")
                        raise ToolFailure(source+" 未能完成请求（HTTP "+str(reply.status_code)+"）。", "provider_error")
                    payload = reply.json()
                    break
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt == 2:
                        raise ToolFailure(source+" 连接超时或网络不可用。", "network_error") from None
                except (ValueError, httpx.HTTPError):
                    raise ToolFailure(source+" 返回了无法使用的数据。", "invalid_response") from None
            stamp = now_utc()
            if ttl:
                self.cache.put(source, args, {"payload":payload,"retrieved_at":stamp.isoformat()})
        self.budget.check()
        ident = "ev-"+stable([source,stamp.isoformat(),payload])[:16]
        record = Evidence(id=ident,source=source,retrieved_at=stamp,
            valid_until=stamp+timedelta(seconds=ttl) if ttl else None,
            tool_call_id=self.call_id,response_sha256=stable(payload),summary=source+" 服务响应")
        self.evidence.setdefault(ident, record)
        self.last_evidence.append(ident)
        return payload, ident

    def geocode(self, query):
        match = re.fullmatch(r"\s*(1\.\d+)\s*,\s*(10[34]\.\d+)\s*", query)
        if match:
            lat, lon = map(float, match.groups())
            if not singapore(lon, lat):
                raise ToolFailure("首版只支持新加坡范围，请修改地点。", "out_of_scope")
            return [Place(id="coordinate-"+stable([lon,lat])[:14],name=query,lat=lat,lon=lon,source="用户明确提供的坐标")]
        search_text = location_query(query)
        payload, ev = self.request("GET",self.settings.geocode_base+"/search","ORS Pelias",
            params={"text":search_text,"boundary.country":"SGP","size":5},
            headers={"Authorization":self.key("ORS_API_KEY")},ttl=86400)
        places = []
        for feature in payload.get("features",[]):
            lon, lat = feature["geometry"]["coordinates"][:2]
            if not singapore(lon,lat):
                continue
            prop = feature.get("properties",{})
            if not location_name_matches(search_text,prop.get("name"),prop.get("label")):
                continue
            ident = prop.get("gid") or stable(feature)[:16]
            places.append(Place(id="pelias:"+ident,name=prop.get("label") or prop.get("name") or query,
                lat=lat,lon=lon,source="ORS Pelias",evidence_ids=[ev],
                requires_confirmation=prop.get("confidence") is None or float(prop["confidence"])<.8))
        return places

    def pois(self, center, category, radius=1000, limit=10, name=None):
        if category not in ("cafe","restaurant","hospital","parking",None) or (category is None and not name):
            raise ToolFailure("不支持该 POI 类别。", "invalid_arguments")
        if radius < 100 or radius > 5000 or limit not in (10,30):
            raise ToolFailure("POI 搜索超出允许范围。", "invalid_arguments")
        selector = f'["amenity"="{category}"]' if category else '["amenity"~"^(cafe|restaurant|hospital)$"]'
        query = f'[out:json][timeout:18][maxsize:67108864];nwr(around:{radius},{center.lat},{center.lon}){selector};out center tags;'
        payload, ev = self.overpass(query,"OSM Overpass")
        places = []
        for el in payload.get("elements",[]):
            coords, tags = el.get("center",el), el.get("tags",{})
            lat, lon = coords.get("lat"), coords.get("lon")
            if lat is None or lon is None or not singapore(lon,lat):
                continue
            label = tags.get("name") or tags.get("name:en") or "OSM "+str(tags.get("amenity") or category or "POI")+" "+str(el["id"])
            if name and name.casefold() not in label.casefold():
                continue
            places.append(Place(id=f'osm:{el["type"]}:{el["id"]}',name=label,lat=lat,lon=lon,
                category=tags.get("amenity",category),opening_hours=tags.get("opening_hours"),source="OSM Overpass",evidence_ids=[ev]))
        places.sort(key=lambda p: distance([center.lon,center.lat],[p.lon,p.lat]))
        return places[:limit]

    def overpass(self,query,source):
        payload,ev = self.request("GET",self.settings.overpass_url,source,params={"data":query},
            headers={"User-Agent":"FYP-RouteAgent/0.2","Accept":"application/json"},ttl=3600)
        if payload.get("remark") or not isinstance(payload.get("elements"),list):
            raise ToolFailure("OSM 查询未完整完成，请稍后重试或调整真实服务端点。","invalid_response")
        return payload,ev

    def roads(self, center, radius=500, limit=30):
        if not 100<=radius<=1500 or not 1<=limit<=30:
            raise ToolFailure("道路查询超出允许范围。","invalid_arguments")
        query = f'[out:json][timeout:18][maxsize:67108864];way(around:{radius},{center.lat},{center.lon})["highway"];out geom tags;'
        payload,ev = self.overpass(query,"OSM roads")
        roads = []
        for el in payload.get("elements",[]):
            geometry = [[p["lon"],p["lat"]] for p in el.get("geometry",[]) if "lon" in p and "lat" in p]
            if el.get("type")!="way" or len(geometry)<2 or not el.get("tags",{}).get("highway"):
                continue
            tags = el["tags"]
            roads.append({"id":"osm:way:"+str(el["id"]),"source":"OSM Overpass","name":tags.get("name"),
                "tags":{k:tags.get(k) for k in ("highway","oneway","maxspeed","access","motor_vehicle","foot","sidewalk","surface")},
                "geometry":{"type":"LineString","coordinates":geometry},"evidence_ids":[ev]})
        roads.sort(key=lambda road:min(distance([center.lon,center.lat],p) for p in road["geometry"]["coordinates"]))
        return {"roads":roads[:limit],"truncated":max(0,len(roads)-limit),"evidence_ids":[ev],
            "basis":"OSM 道路标签；缺失属性未知，不表示现场可通行或已封路"}

    def opening(self, place):
        match = re.fullmatch(r"osm:(node|way|relation):(\d+)",place.id)
        if not match:
            raise ToolFailure("该地点没有 OSM 身份，不能编造营业信息。", "missing_evidence")
        query = f'[out:json][timeout:18][maxsize:67108864];{match[1]}({match[2]});out center tags;'
        payload, ev = self.overpass(query,"OSM opening hours")
        elements = payload.get("elements",[])
        if not elements:
            raise ToolFailure("OSM 未返回此地点的营业信息。", "missing_evidence")
        place.opening_hours = elements[0].get("tags",{}).get("opening_hours")
        place.evidence_ids = list(dict.fromkeys([*place.evidence_ids,ev]))
        return place

    def routes(self, origin, destination, mode, avoid_highways=False, count=3, start_coordinate=None):
        if mode not in ("driving","walking") or not 1 <= count <= 3:
            raise ToolFailure("路线交通方式或替代数量不合法。", "invalid_arguments")
        coordinates = [start_coordinate or [origin.lon,origin.lat],[destination.lon,destination.lat]]
        base = self.settings.osrm_driving_base if mode=="driving" else self.settings.osrm_walking_base
        profile = self.settings.osrm_driving_profile if mode=="driving" else self.settings.osrm_walking_profile
        if not base.strip():
            raise ToolFailure("请在 .env 配置对应交通方式的 OSRM 服务端点。","missing_configuration")
        if not re.fullmatch(r"[A-Za-z0-9_-]+",profile):
            raise ToolFailure("OSRM profile 配置不合法。","invalid_configuration")
        if mode=="walking" and base.rstrip("/")==self.settings.osrm_driving_base.rstrip("/"):
            raise ToolFailure("步行必须使用独立 foot 路网实例，不能仅修改驾车服务的 profile 名。","invalid_configuration")
        if any(len(p)!=2 or not singapore(*p) for p in coordinates):
            raise ToolFailure("OSRM 请求坐标必须是有效的新加坡经纬度。","invalid_arguments")
        params = {"alternatives":str(count-1) if count>1 else "false","steps":"true",
                  "geometries":"geojson","overview":"full","radiuses":"150;150"}
        if avoid_highways and mode=="driving":
            params["exclude"] = "motorway"
        path = ";".join(",".join(str(v) for v in p) for p in coordinates)
        payload, ev = self.request("GET",base.rstrip("/")+"/route/v1/"+profile+"/"+path,
            "OSRM Route",params=params,headers={"User-Agent":"FYP-RouteAgent/0.2 (local academic trip planner)",
            "Accept":"application/json"},ttl=300,paced=True)
        if not isinstance(payload,dict) or payload.get("code")!="Ok":
            code = payload.get("code") if isinstance(payload,dict) else None
            if code in ("NoRoute","NoSegment"):
                raise ToolFailure("OSRM 当前路网未找到可行路线或地点入口。","no_route")
            if code in ("InvalidValue","InvalidOptions") and "exclude" in params:
                raise ToolFailure("此 OSRM 实例不支持避高速排除选项；没有改用普通路线。","unsupported_feature")
            raise ToolFailure("OSRM 未返回成功的路线结果。","invalid_response")
        if not isinstance(payload.get("routes"),list) or not payload["routes"]:
            raise ToolFailure("OSRM 成功响应缺少路线。","invalid_response")
        legs = []
        for route in payload["routes"][:count]:
            geometry = route.get("geometry",{})
            coords = geometry.get("coordinates",[])
            if geometry.get("type")!="LineString" or len(coords)<2 or any(
                not isinstance(p,list) or len(p)<2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p[:2]) for p in coords) or not all(
                isinstance(route.get(k),(int,float)) and math.isfinite(route[k]) and route[k]>=0 for k in ("distance","duration")):
                raise ToolFailure("OSRM 路线几何或汇总数据不合法。", "invalid_response")
            if distance(coordinates[0],coords[0]) > 150 or distance(coordinates[1],coords[-1]) > 150:
                raise ToolFailure("OSRM 返回路线与请求地点入口不一致。", "invalid_response")
            steps = [s for leg in route.get("legs",[]) for s in leg.get("steps",[])]
            if any(s.get("mode") not in (None,"walking","ferry","pushing bike") for s in steps) and mode=="walking":
                raise ToolFailure("步行服务返回了其他交通方式，拒绝标为步行。","invalid_response")
            if mode=="driving" and any(s.get("mode") not in (None,"driving") for s in steps):
                raise ToolFailure("驾车服务含未建模的其他交通方式。","unsupported_feature")
            if mode=="walking" and any(s.get("mode")=="ferry" for s in steps):
                raise ToolFailure("步行服务含渡轮段，不能当作全程步行。","unsupported_feature")
            if "exclude" in params and any("motorway" in i.get("classes",[]) for s in steps for i in s.get("intersections",[])):
                raise ToolFailure("避高速结果仍含 motorway 标记，拒绝发布。","invalid_response")
            segments,cursor = [],0
            for step in steps:
                points = step.get("geometry",{}).get("coordinates",[])
                if len(points)<2:
                    continue
                start = next((i for i in range(cursor,len(coords)) if distance(coords[i],points[0])<.1),None)
                end = next((i for i in range(start+1,len(coords)) if distance(coords[i],points[-1])<.1),None) if start is not None else None
                if end is not None:
                    segments.append({"start":start,"end":end,"name":step.get("name",""),
                                     "duration_s":step.get("duration"),"distance_m":step.get("distance")})
                    cursor = end
            legs.append(RouteLeg(id="leg-"+stable(["OSRM",base,profile,mode,coords,route["duration"],origin.id,destination.id])[:16],mode=mode,
                origin=origin,destination=destination,source="OSRM",geometry=geometry,distance_m=route["distance"],
                provider_duration_s=route["duration"],evidence_ids=[ev],segments=segments,
                highways=False if avoid_highways and mode=="driving" else None))
        return legs

    def lta(self, endpoint, ttl, *, reserve_http=0, allow_partial=False):
        values, refs, fingerprints = [], [], set()
        pages,skip = 0,0
        self.lta_status[endpoint] = {"complete":False,"pages":0,"record_count":0,"reason":None}
        try:
            while True:
                self.budget.check()
                if self.budget.http_calls>=self.settings.max_http_calls-reserve_http or (
                    allow_partial and self.budget.remaining()<30):
                    raise ToolFailure("已为后续算路和模型决策保留预算；LTA 数据尚未读完。","pagination_budget")
                payload, ev = self.request("GET",self.settings.lta_base+"/"+endpoint,"LTA "+endpoint,
                    params={"$skip":skip},headers={"AccountKey":self.key("LTA_API_KEY"),"accept":"application/json"},ttl=ttl)
                if not isinstance(payload,dict) or not isinstance(payload.get("value"),list):
                    raise ToolFailure("LTA 返回的数据结构无效。", "invalid_response")
                batch = payload["value"]
                if len(batch)>500 or any(not isinstance(row,dict) for row in batch):
                    raise ToolFailure("LTA 分页数据大小或记录格式无效。","invalid_response")
                fingerprint = stable(batch)
                if batch and fingerprint in fingerprints:
                    raise ToolFailure("LTA 返回了重复分页，不能把它当作完整数据。","repeated_page")
                fingerprints.add(fingerprint)
                refs.append(ev)
                values.extend(batch)
                pages+=1
                self.lta_status[endpoint].update(pages=pages,record_count=len(values))
                if len(batch)<500:
                    self.lta_status[endpoint]["complete"] = True
                    return values,refs
                skip+=500
        except ToolFailure as error:
            self.lta_status[endpoint]["reason"] = str(error)
            if not allow_partial or not values:
                raise
            # These are actual successful pages, never an inferred island-wide snapshot.
            return values,refs

    def traffic(self):
        bands, a = self.lta("v4/TrafficSpeedBands",300,reserve_http=20,allow_partial=True)
        try:
            incidents, b = self.lta("TrafficIncidents",120,reserve_http=18,allow_partial=True)
        except ToolFailure as error:
            incidents,b = [],[]
            self.lta_status["TrafficIncidents"] = {"complete":False,"pages":0,"record_count":0,"reason":str(error)}
        retrieved = min(self.evidence[e].retrieved_at for e in a)
        return {"speed_bands":bands,"incidents":incidents,"retrieved_at":retrieved.isoformat(),"evidence_ids":a+b,
                "collection":{key:dict(value) for key,value in self.lta_status.items() if key in ("v4/TrafficSpeedBands","TrafficIncidents")}}

    def parking(self, near, name=None):
        records, refs = self.lta("CarParkAvailabilityv2",60)
        places = []
        for item in records:
            if item.get("LotType") != "C":
                continue
            parts = str(item.get("Location","")).split()
            if len(parts) != 2:
                continue
            lat, lon = map(float,parts)
            label = item.get("Development") or item.get("CarParkID","")
            if not singapore(lon,lat) or (name and name.casefold() not in label.casefold()):
                continue
            try:
                available = int(item["AvailableLots"])
                if available<0:
                    available = None
            except (KeyError,TypeError,ValueError):
                available = None
            places.append(Place(id="lta-parking:"+str(item["CarParkID"]),name=label,lat=lat,lon=lon,
                source="LTA CarParkAvailabilityv2",category="parking",available_lots=available,
                evidence_ids=refs))
        places.sort(key=lambda p:distance([p.lon,p.lat],[near.lon,near.lat]))
        unique = {p.id:p for p in places if distance([p.lon,p.lat],[near.lon,near.lat]) <= 3000}
        return list(unique.values())[:10]

    def weather(self, points, departure=None):
        # Public non-commercial Open-Meteo endpoint needs no credential.
        unique = list(dict.fromkeys(tuple(p) for p in points))
        if not unique or len(unique)>8 or any(not singapore(lon,lat) for lon,lat in unique):
            raise ToolFailure("天气查询需要 1–8 个已查询的真实新加坡坐标。","invalid_arguments")
        params = {"latitude":",".join(str(p[1]) for p in unique),"longitude":",".join(str(p[0]) for p in unique),
            "hourly":"precipitation_probability,precipitation,weather_code","forecast_days":7,
            "timeformat":"unixtime","timezone":"UTC","precipitation_unit":"mm"}
        payload,ev = self.request("GET",self.settings.open_meteo_base+"/forecast","Open-Meteo forecast",params=params,ttl=300)
        records = payload if isinstance(payload,list) else [payload]
        if len(records)!=len(unique):
            raise ToolFailure("Open-Meteo 返回的坐标数量不一致。","invalid_response")
        normalized = []
        for requested,record in zip(unique,records):
            hourly = record.get("hourly",{})
            units = record.get("hourly_units",{})
            times = hourly.get("time",[])
            if not times or units.get("precipitation_probability")!="%" or units.get("precipitation")!="mm":
                raise ToolFailure("Open-Meteo 预报缺失或单位不正确。","invalid_response")
            variables = [hourly.get(k,[]) for k in ("precipitation_probability","precipitation","weather_code")]
            if any(len(v)!=len(times) for v in variables):
                raise ToolFailure("Open-Meteo 预报时间与数值不一致。","invalid_response")
            hours = []
            for index,stamp in enumerate(times):
                end = datetime.fromtimestamp(stamp,timezone.utc)
                probability,mm,code = [v[index] for v in variables]
                if probability is not None and (not math.isfinite(probability) or not 0<=probability<=100) or mm is not None and (not math.isfinite(mm) or mm<0):
                    raise ToolFailure("Open-Meteo 返回无效降水指标。","invalid_response")
                hours.append({"start":(end-timedelta(hours=1)).isoformat(),"end":end.isoformat(),
                    "precipitation_probability":probability,"precipitation_mm":mm,"weather_code":code})
            normalized.append({"requested_coordinate":list(requested),"grid_coordinate":[record["longitude"],record["latitude"]],"hours":hours})
        return {"provider":"open_meteo","points":normalized,"evidence_ids":[ev],
            "basis":"小时降水指标对应标注时间之前的一小时；天气代码是标注时刻值。天气模型网格预报，非路面观测"}

    def nea_weather(self):
        key = self.settings.credential("DATAGOVSG_API_KEY")
        payload, ev = self.request("GET",self.settings.weather_base+"/two-hr-forecast","NEA two-hour forecast",
            headers={"x-api-key":key} if key else {},ttl=60)
        if payload.get("code") != 0 or not payload.get("data",{}).get("items"):
            raise ToolFailure("NEA 暂未返回有效预报。", "missing_evidence")
        for item in payload["data"]["items"]:
            if item.get("timestamp"):
                self.evidence[ev].observed_at = datetime.fromisoformat(item["timestamp"])
        ends = [datetime.fromisoformat(item["valid_period"]["end"]) for item in payload["data"]["items"] if item.get("valid_period",{}).get("end")]
        self.evidence[ev].valid_until = max(ends) if ends else None
        return {"data":payload["data"],"evidence_ids":[ev]}
