"""Real OSRM routes, ORS geocoding, OSM, LTA and weather. No routing fallback."""
import hashlib
import json
import math
import re
import time
import threading
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
import httpx
from .cache import JsonCache
from .models import Evidence, Place, RouteLeg
from .tools import ToolFailure
from .poi_categories import category_tag, POI_KEYS

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
        text = " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))
        return re.sub(r"\bcheck point\b", "checkpoint", text)
    needle = words(query)
    if re.search(r"[\u3400-\u9fff]",needle):
        # Chinese prose does not separate an entity from its surrounding
        # sentence with spaces. Preserve the exact phrase, not Latin boundaries.
        return bool(needle) and any(needle in words(name) for name in names if isinstance(name,str))
    return bool(needle) and any(" "+needle+" " in " "+words(name)+" " for name in names if isinstance(name,str))

def location_name_similar(query, *names):
    """Allow reordered words and small Latin typos, but retain every query word."""
    def words(value):
        return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())
    wanted = words(query)
    if not wanted:
        return False
    for name in names:
        if not isinstance(name, str):
            continue
        available = words(name)
        # Consume exact tokens first so fuzzy words cannot steal a required word.
        remaining = []
        for word in wanted:
            if word in available:
                available.remove(word)
            else:
                remaining.append(word)
        for word in remaining:
            matches = [(SequenceMatcher(None, word, candidate).ratio(), candidate)
                for candidate in available if word.isascii() and word.isalpha() and len(word)>=5
                and candidate.isascii() and candidate.isalpha() and len(candidate)>=5]
            score, candidate = max(matches, default=(0, ""))
            if score<.82:
                break
            available.remove(candidate)
        else:
            return True
    return False

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
        self.lta_progress = {}
        self.request_deadline = None
    def key(self, name):
        key = self.settings.credential(name)
        if not key:
            raise ToolFailure("请在 .env 中配置 " + name + "。", "missing_credential")
        return key
    def transit_routes(self, origin, destination, request):
        from .transit import transit_routes
        return transit_routes(self,origin,destination,request)

    def transit_hubs(self, center, kind="rail", radius=2000, limit=3):
        """Real access locations; discovery is separate from travel feasibility."""
        if kind not in ("rail","bus") or not 100<=radius<=5000 or not 1<=limit<=10:
            raise ToolFailure("接驳站点查询参数不合法。","invalid_arguments")
        endpoint="getNearestMrtStops" if kind=="rail" else "getNearestBusStops"
        base=self.settings.onemap_base.removesuffix("/routingsvc").rstrip("/")
        payload,ev=self.request("GET",base+"/nearbysvc/"+endpoint,"OneMap nearby transport",
            params={"latitude":center.lat,"longitude":center.lon,"radius_in_meters":radius},
            headers={"Authorization":self.key("ONEMAP_TOKEN").removeprefix("Bearer ")},ttl=86400)
        if not isinstance(payload,list):
            raise ToolFailure("OneMap 接驳站点响应结构无效。","invalid_response")
        places=[]
        try:
            for item in payload:
                lat,lon=float(item["lat"]),float(item["lon"])
                if not singapore(lon,lat) or distance([center.lon,center.lat],[lon,lat])>radius+20:
                    raise ValueError("Invalid hub coordinates")
                places.append(Place(id="onemap-hub:"+kind+":"+str(item["id"]),name=item["name"],lat=lat,lon=lon,
                    category="transit_hub",source="OneMap nearby transport",address=item.get("road"),evidence_ids=[ev]))
        except (KeyError,TypeError,ValueError):
            raise ToolFailure("OneMap 接驳站点数据无效。","invalid_response") from None
        places.sort(key=lambda p:distance([center.lon,center.lat],[p.lon,p.lat]))
        return places[:limit]
    def company_locations(self, name):
        from .company_locations import company_locations
        return company_locations(self,name)
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

    def request(self, method, url, source, *, params=None, body=None, data=None, headers=None, ttl=0, paced=False, response_format="json"):
        args = {"method":method,"url":url,"params":params,"body":body,"data":data}
        if response_format!="json":
            args["response_format"] = response_format
        cached = self.cache.get(source, args, ttl) if ttl else None
        if cached:
            payload, stamp = cached["payload"], datetime.fromisoformat(cached["retrieved_at"])
            self.cache_hits += 1
        else:
            payload = None
            for attempt in range(3):
                try:
                    remaining = min(self.budget.remaining(),self.request_deadline-time.monotonic()) if self.request_deadline else self.budget.remaining()
                    if remaining<=0:
                        raise ToolFailure("已达到本次交通查询时限，保留已取得的数据。","pagination_budget")
                    with httpx.Client(timeout=max(.1, min(20, remaining)), trust_env=False,
                                      follow_redirects=False) as client:
                        reply = self.send_request(client,method,url,paced=paced,params=params,json=body,data=data,headers=headers)
                    if reply.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        self.budget.check()
                        time.sleep(min(2**attempt, max(0, remaining)))
                        continue
                    if reply.status_code in (401, 403):
                        raise ToolFailure(source+" 拒绝访问，请检查 Key、服务权限或配额。", "authentication_error")
                    if reply.status_code == 429:
                        raise ToolFailure(source+" 配额已用尽，请稍后重试。", "rate_limited")
                    if reply.status_code >= 400:
                        if source=="OneMap transit" and reply.status_code==404:
                            try:
                                message=reply.json().get("error","")
                            except (ValueError,AttributeError):
                                message=""
                            if isinstance(message,str) and "no route found" in message.lower():
                                raise ToolFailure("OneMap 在这些端点和时段未找到路线；可查询真实车站并验证步行接驳，不能据此判断所有组合不可达。","no_route")
                        if source=="OSRM Route" and reply.status_code==400:
                            code = reply.json().get("code")
                            if code in ("NoRoute","NoSegment"):
                                raise ToolFailure("OSRM 当前路网未找到可行路线或地点入口。","no_route")
                            if code in ("InvalidValue","InvalidOptions") and params and params.get("exclude"):
                                raise ToolFailure("此 OSRM 实例不支持避高速排除选项；没有改用普通路线。","unsupported_feature")
                        raise ToolFailure(source+" 未能完成请求（HTTP "+str(reply.status_code)+"）。", "provider_error")
                    if response_format=="text" and len(reply.content)>2_000_000:
                        raise ToolFailure("地点来源网页超过读取上限。","invalid_response")
                    payload = {"text":reply.text} if response_format=="text" else reply.json()
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

    def web_location_sources(self, query):
        from .web_locations import search_sources
        return search_sources(self,query)

    def remember_location_source(self, query, url):
        from .web_locations import remember_source
        return remember_source(self,query,url)

    def web_location_candidate(self, document_id, place_name, company_name, quote):
        from .web_locations import source_candidate
        return source_candidate(self,document_id,place_name,company_name,quote)

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
            checkpoint = bool(re.search(r"\bcheck\s*point\b",search_text,re.I))
            if checkpoint and not re.search(r"\bblock\b",search_text,re.I) and re.search(r"\bblock\s+[A-Z]?\d",prop.get("name", ""),re.I):
                continue  # Generic checkpoint queries must not return individual clearance blocks.
            exact = location_name_matches(search_text,prop.get("name"),prop.get("label"))
            if not exact and not location_name_similar(search_text,prop.get("name"),prop.get("label")):
                continue
            ident = prop.get("gid") or stable(feature)[:16]
            places.append(Place(id="pelias:"+ident,name=prop.get("label") or prop.get("name") or query,
                lat=lat,lon=lon,source="ORS Pelias",evidence_ids=[ev],
                requires_confirmation=checkpoint or not exact or prop.get("confidence") is None or float(prop["confidence"])<.8,
                location_kind="checkpoint" if checkpoint else prop.get("layer"),
                access_note="口岸地图位置不等于实际出关后的乘车点，请确认出发位置可通行。" if checkpoint else None))
        # Pelias can tokenize the compound "checkpoint" as a generic area
        # search. Retry its spaced spelling without accepting that area as a
        # substitute. This changes spelling, never the requested landmark.
        spaced = re.sub(r"\bcheckpoint\b", "check point", search_text, flags=re.I)
        if not places and spaced!=search_text:
            places = self.geocode(spaced)
            for place in places:
                place.requires_confirmation = True
        # Keep a venue instead of an adjacent street carrying the same label.
        places = [place for place in places if not (":street:" in place.id and any(
            ":venue:" in other.id and other.name==place.name and distance([other.lon,other.lat],[place.lon,place.lat])<200
            for other in places))]
        return places

    def pois(self, center, category, radius=1000, limit=10, name=None):
        return self._pois([[center.lon,center.lat]],category,radius,limit,name)

    def pois_along_route(self, reference, category, radius=1000, limit=10, name=None):
        from bisect import bisect_left
        coords=reference.geometry.get("coordinates",[])
        if reference.geometry.get("type")=="MultiLineString":
            coords=[point for leg in reference.geometry["coordinates"] for point in leg]
        elif reference.geometry.get("type")!="LineString":
            coords=[]
        if len(coords)<2:
            raise ToolFailure("沿途搜索需要真实分段路线几何。","missing_evidence")
        if not any(self.evidence.get(e) and self.evidence[e].source in ("OSRM Route","OneMap transit") for e in reference.evidence_ids):
            raise ToolFailure("沿途搜索缺少真实路线服务证据。","missing_evidence")
        if any(len(p)<2 or not singapore(p[0],p[1]) for p in coords):
            raise ToolFailure("沿途路线坐标无效。","invalid_response")
        cumulative=[0]
        for a,b in zip(coords,coords[1:]):
            cumulative.append(cumulative[-1]+distance(a,b))
        # Sample only service-returned vertices, never an invented straight
        # origin/destination corridor. Keep the external search bounded.
        centers=list(dict.fromkeys(tuple(coords[min(bisect_left(cumulative,cumulative[-1]*n/4),len(coords)-1)][:2])
            for n in range(5)))
        places=self._pois(centers,category,radius,limit,name,reference.origin,reference.destination)
        for place in places:
            place.evidence_ids=list(dict.fromkeys([*place.evidence_ids,*reference.evidence_ids]))
        return places

    def _pois(self, centers, category, radius, limit, name, origin=None, destination=None):
        search_names=name if isinstance(name,list) else [name] if name else []
        tag = category_tag(category) if category else None
        if not category and not name:
            raise ToolFailure("请提供经停类别或用户指定名称。", "invalid_arguments")
        if radius < 100 or radius > 5000 or limit not in (10,30):
            raise ToolFailure("POI 搜索超出允许范围。", "invalid_arguments")
        selectors = [f'["{tag[0]}"="{tag[1]}"]'] if tag else [f'["{key}"]' for key in POI_KEYS]
        statements="".join(f'nwr(around:{radius},{lat},{lon}){selector};' for lon,lat in centers for selector in selectors)
        query = '[out:json][timeout:18][maxsize:67108864];('+statements+');out center tags;'
        payload, ev = self.overpass(query,"OSM Overpass")
        places = []
        for el in payload.get("elements",[]):
            coords, tags = el.get("center",el), el.get("tags",{})
            lat, lon = coords.get("lat"), coords.get("lon")
            if lat is None or lon is None or not singapore(lon,lat):
                continue
            if tag and tags.get(tag[0])!=tag[1]:
                continue
            actual_category=category if tag else next(("bakery" if key=="shop" and tags[key]=="bakery" else
                tags[key] if key=="amenity" else key+":"+tags[key] for key in POI_KEYS if
                isinstance(tags.get(key),str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}",tags[key])),None)
            if actual_category is None:
                continue
            label = tags.get("name") or tags.get("name:en") or "OSM "+str(tags.get("amenity") or category or "POI")+" "+str(el["id"])
            names=list(dict.fromkeys([label,*[v for k,v in tags.items() if isinstance(v,str) and (
                k in ("brand","alt_name","official_name") or re.fullmatch(r"(?:name|brand|alt_name|official_name):[a-z]{2,3}(?:-[A-Za-z]+)?",k))]]))
            if search_names and not any(location_name_matches(q,n) for q in search_names for n in names):
                continue
            places.append(Place(id=f'osm:{el["type"]}:{el["id"]}',name=label,names=names,lat=lat,lon=lon,
                category=actual_category,opening_hours=tags.get("opening_hours"),source="OSM Overpass",evidence_ids=[ev],
                address=tags.get("addr:full") or " ".join(str(tags[k]) for k in ("addr:housenumber","addr:street","addr:postcode") if tags.get(k)) or None,
                source_url=f'https://www.openstreetmap.org/{el["type"]}/{el["id"]}'))
        if origin and destination:
            # Coarse shortlist only. Actual detour time comes from later OSRM
            # routes; this distance never validates arrival or congestion.
            places.sort(key=lambda p: distance([origin.lon,origin.lat],[p.lon,p.lat])+
                distance([p.lon,p.lat],[destination.lon,destination.lat]))
        else:
            places.sort(key=lambda p: min(distance(center,[p.lon,p.lat]) for center in centers))
        return list({p.id:p for p in places}.values())[:limit]

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

    def lta(self, endpoint, ttl, *, reserve_http=0, allow_partial=False, deadline=None, http_limit=None, resume=False):
        values, refs, fingerprints = [], [], set()
        pages,skip = 0,0
        progress = self.lta_progress.get(endpoint) if resume else None
        if progress:
            valid=all(self.evidence.get(e) and self.evidence[e].valid_until and self.evidence[e].valid_until>=now_utc() for e in progress["refs"])
            if valid:
                values,refs,fingerprints = list(progress["values"]),list(progress["refs"]),set(progress["fingerprints"])
                pages,skip=progress["pages"],progress["skip"]
                if self.lta_status[endpoint]["complete"]:
                    return values,refs
        initial_http = self.budget.http_calls
        self.lta_status[endpoint] = {"complete":False,"pages":pages,"record_count":len(values),"reason":None,"next_skip":skip}
        try:
            while True:
                self.budget.check()
                if (deadline is not None and time.monotonic()>=deadline) or (
                        http_limit is not None and self.budget.http_calls-initial_http>=http_limit):
                    raise ToolFailure("交通查询已达到时间或请求上限；仅使用已取得的部分数据。","pagination_budget")
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
                skip+=len(batch)
                self.lta_status[endpoint]["next_skip"]=skip
                self.lta_progress[endpoint]={"values":list(values),"refs":list(refs),"fingerprints":set(fingerprints),"pages":pages,"skip":skip}
                if len(batch)<500:
                    self.lta_status[endpoint]["complete"] = True
                    return values,refs
        except ToolFailure as error:
            self.lta_status[endpoint]["reason"] = str(error)
            if not allow_partial or not values:
                raise
            # These are actual successful pages, never an inferred island-wide snapshot.
            return values,refs

    def traffic(self, continue_collection=False):
        previous = self.request_deadline
        self.request_deadline = time.monotonic()+self.settings.traffic_max_seconds
        try:
            bands, a = self.lta("v4/TrafficSpeedBands",300,reserve_http=20,allow_partial=True,
                deadline=self.request_deadline,http_limit=self.settings.traffic_max_http_calls,resume=continue_collection)
            # Incidents must get their own opportunity even when speed-band
            # paging reaches its limit; still inside the end-to-end budget.
            self.request_deadline=time.monotonic()+min(3,max(0,self.budget.remaining()-20))
            try:
                incidents, b = self.lta("TrafficIncidents",120,reserve_http=18,allow_partial=True,
                    deadline=self.request_deadline,http_limit=1,resume=continue_collection)
            except ToolFailure as error:
                incidents,b = [],[]
                self.lta_status["TrafficIncidents"] = {"complete":False,"pages":0,"record_count":0,"reason":str(error)}
        finally:
            self.request_deadline = previous
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
