"""OneMap public-transport itineraries and returned SGD fares; never local guesses."""
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from .models import Place, RouteLeg, RouteCandidate
from .tools import ToolFailure
from .providers import stable, singapore, distance


def decode_polyline(encoded):
    if not isinstance(encoded,str) or not encoded or len(encoded)>300000:
        raise ValueError("Invalid geometry")
    values, current, shift = [], 0, 0
    for char in encoded:
        number = ord(char)-63
        if not 0<=number<=63 or shift>30:
            raise ValueError("Invalid polyline")
        current |= (number & 31)<<shift
        if number<32:
            values.append(~(current>>1) if current&1 else current>>1)
            current,shift = 0,0
        else:
            shift+=5
    if shift or len(values)%2:
        raise ValueError("Incomplete polyline")
    lat=lon=0
    points=[]
    for i in range(0,len(values),2):
        lat+=values[i]; lon+=values[i+1]
        point=[lon/1e5,lat/1e5]
        if not singapore(*point):
            raise ValueError("Geometry outside Singapore")
        points.append(point)
    if len(points)<2:
        raise ValueError("Missing route geometry")
    return points


def transit_routes(provider, origin, destination, request):
    token=provider.key("ONEMAP_TOKEN")
    stamp=request.departure_time.astimezone(ZoneInfo("Asia/Singapore"))
    # The live service normalizes time to minute resolution. Round upward so
    # onward itineraries cannot board before access walking / dwell completes.
    if stamp.second or stamp.microsecond:
        stamp=stamp.replace(second=0,microsecond=0)+timedelta(minutes=1)
    params={"start":f"{origin.lat},{origin.lon}","end":f"{destination.lat},{destination.lon}",
        "routeType":"pt","date":stamp.strftime("%m-%d-%Y"),"time":stamp.strftime("%H:%M:%S"),
        "mode":"TRANSIT","numItineraries":3}
    if request.max_walking_m is not None:
        params["maxWalkDistance"]=request.max_walking_m
    payload,ev=provider.request("GET",provider.settings.onemap_base.rstrip("/")+"/route","OneMap transit",
        params=params,headers={"Authorization":token.removeprefix("Bearer ")},ttl=60)
    if payload.get("error"):
        raise ToolFailure("OneMap 未返回可用公共交通路线，请检查地点和出发时间。","no_route")
    itineraries=payload.get("plan",{}).get("itineraries")
    if not isinstance(itineraries,list):
        raise ToolFailure("OneMap 公共交通响应结构无效。","invalid_response")
    if not itineraries:
        raise ToolFailure("OneMap 在此地点和出发时段未找到公共交通路线。","no_route")
    routes=[]
    rejected=0
    for itinerary in itineraries[:3]:
        try:
            legs=[]
            for item in itinerary["legs"]:
                mode={"WALK":"walking","BUS":"bus","SUBWAY":"rail","RAIL":"rail","TRAM":"rail"}.get(item["mode"])
                if not mode:
                    raise ValueError("Unsupported transit leg")
                coords=decode_polyline(item["legGeometry"]["points"])
                def endpoint(key):
                    value=item[key]
                    if not singapore(value["lon"],value["lat"]):
                        raise ValueError("Invalid endpoint")
                    return Place(id="onemap-stop:"+stable([value.get("stopId"),value["lat"],value["lon"]])[:16],
                        name=value["name"],lat=value["lat"],lon=value["lon"],source="OneMap transit",evidence_ids=[ev],
                        stop_code=str(value["stopCode"]) if value.get("stopCode") is not None else None,
                        source_stop_id=str(value["stopId"]) if value.get("stopId") is not None else None)
                a,b=endpoint("from"),endpoint("to")
                start=datetime.fromtimestamp(item["startTime"]/1000,timezone.utc)
                end=datetime.fromtimestamp(item["endTime"]/1000,timezone.utc)
                duration=(end-start).total_seconds()
                meters=float(item["distance"])
                if duration<0 or not math.isfinite(meters) or meters<0:
                    raise ValueError("Invalid metrics")
                if distance(coords[0],[a.lon,a.lat])>150 or distance(coords[-1],[b.lon,b.lat])>150:
                    raise ValueError("Geometry does not match stops")
                legs.append(RouteLeg(id="transit-leg:"+stable([item,ev])[:16],mode=mode,origin=a,destination=b,
                    geometry={"type":"LineString","coordinates":coords},distance_m=meters,provider_duration_s=duration,
                    source="OneMap transit",evidence_ids=[ev],service_name=item.get("routeShortName") or item.get("route") or None,
                    source_route_id=str(item["routeId"]) if item.get("routeId") is not None else None,
                    source_trip_id=str(item["tripId"]) if item.get("tripId") is not None else None,
                    departure_time=start,arrival_time=end))
            connected=[]
            for leg in legs:
                if connected:
                    previous=connected[-1]
                    gap=distance([previous.destination.lon,previous.destination.lat],[leg.origin.lon,leg.origin.lat])
                    if gap>2:
                        # Some returned rail→bus transfers omit their foot leg.
                        # Query a real foot path and fit it inside the schedule;
                        # never assume this gap takes no time or draw a line.
                        try:
                            connector=provider.routes(previous.destination,leg.origin,"walking",False,1)[0].model_copy(deep=True)
                        except ToolFailure as error:
                            if error.code=="no_route":
                                raise ValueError("Transfer foot route unavailable") from None
                            raise
                        connector.departure_time=previous.arrival_time
                        connector.arrival_time=connector.departure_time+timedelta(seconds=connector.provider_duration_s)
                        if connector.arrival_time>leg.departure_time:
                            raise ValueError("Transfer foot route misses scheduled boarding")
                        connected.append(connector)
                connected.append(leg)
            legs=connected
            if not legs or not any(leg.mode in ("bus","rail") for leg in legs):
                raise ValueError("No public transport legs")
            if any(a.arrival_time>b.departure_time for a,b in zip(legs,legs[1:])):
                raise ValueError("Invalid transfer timing")
            start=datetime.fromtimestamp(itinerary["startTime"]/1000,timezone.utc)
            end=datetime.fromtimestamp(itinerary["endTime"]/1000,timezone.utc)
            if abs((start-legs[0].departure_time).total_seconds())>1 or abs((end-legs[-1].arrival_time).total_seconds())>1:
                raise ValueError("Itinerary/leg timing mismatch")
            # Check against the sent minute, including any upward rounding.
            if start<stamp or end<=start:
                raise ValueError("Itinerary departs before requested time")
            if distance(legs[0].geometry["coordinates"][0],[origin.lon,origin.lat])>150 or distance(
                    legs[-1].geometry["coordinates"][-1],[destination.lon,destination.lat])>150:
                raise ValueError("Itinerary does not match requested endpoints")
            total=(end-request.departure_time).total_seconds()  # Includes initial waiting and transfers.
            raw_fare=itinerary.get("fare")
            fare=float(raw_fare) if isinstance(raw_fare,(str,int,float)) and not isinstance(raw_fare,bool) and raw_fare!="" else None
            if fare is not None and (not math.isfinite(fare) or fare<0):
                raise ValueError("Invalid fare")
            walking=[leg for leg in legs if leg.mode=="walking"]
            refs=list(dict.fromkeys([ev,*[e for leg in legs for e in leg.evidence_ids],*origin.evidence_ids,*destination.evidence_ids]))
            routes.append(RouteCandidate(id="transit-route:"+stable([itinerary,origin.id,destination.id,stamp.isoformat()])[:16],
                mode="transit",origin=origin,destination=destination,legs=legs,
                geometry={"type":"MultiLineString","coordinates":[leg.geometry["coordinates"] for leg in legs]},
                distance_m=sum(leg.distance_m for leg in legs),walking_m=sum(leg.distance_m for leg in walking),
                walking_s=sum(leg.provider_duration_s for leg in walking),provider_total_s=total,total_s=total,
                time_basis="OneMap 公交／地铁服务预计时间，含候车与换乘；非准时保证",evidence_ids=refs,
                fare_sgd=fare,fare_evidence_ids=[ev] if fare is not None else [],
                fare_basis="OneMap 返回的公共交通参考票价（SGD），实际支付规则需核实" if fare is not None else "OneMap 未返回完整票价，费用未知"))
        except (KeyError,TypeError,ValueError,OverflowError):
            rejected+=1
    if not routes:
        raise ToolFailure("OneMap 路线的几何、时刻或票价数据无效；未生成替代路线。","invalid_response") from None
    if rejected:
        for route in routes:
            route.source_warnings.append(f"OneMap 有 {rejected} 条候选的几何、时刻或票价数据无效，未参与比较；保留其他有效候选。")
    return routes


def transit_via(provider, origin, destination, poi, request):
    """Query each onward trip after actual service arrival plus confirmed dwell.

    Bus/rail transfers stay inside provider itineraries. A short business-stop
    access trip may use a real foot route if the PT service reports no route.
    Separate tickets are not summed into a fictitious integrated fare.
    """
    if request.stop_duration_s is None:
        raise ToolFailure("经停需要确认停留时间，不能从到达时限推断。","missing_information")

    def journeys(a,b,departure):
        onward=request.model_copy(update={"departure_time":departure})
        try:
            return provider.transit_routes(a,b,onward)
        except ToolFailure as error:
            if error.code!="no_route":
                raise
            from .routing import assemble
            options=[]
            for leg in provider.routes(a,b,"walking",False,3):
                leg=leg.model_copy(deep=True)
                leg.departure_time=departure
                leg.arrival_time=departure+timedelta(seconds=leg.provider_duration_s)
                options.append(assemble([leg],onward.model_copy(update={"mode":"walking"}),a,b))
            return options

    output=[]
    failures=[]
    for first in journeys(origin,poi,request.departure_time)[:3]:
        arrival=first.legs[-1].arrival_time
        departure=arrival+timedelta(seconds=request.stop_duration_s)
        try:
            endings=journeys(poi,destination,departure)
        except ToolFailure as error:
            if error.code!="no_route":
                raise
            failures.append(error)
            continue
        for second in endings[:3]:
            legs=[*first.legs,*second.legs]
            if not any(l.mode in ("bus","rail") for l in legs):
                continue
            total=(legs[-1].arrival_time-request.departure_time).total_seconds()
            walking=[l for l in legs if l.mode=="walking"]
            transit_parts=[r for r in (first,second) if r.mode=="transit"]
            fare=transit_parts[0].fare_sgd if len(transit_parts)==1 else None
            refs=list(dict.fromkeys([*first.evidence_ids,*second.evidence_ids,*poi.evidence_ids]))
            output.append(RouteCandidate(id="transit-via:"+stable([first.id,second.id,poi.id,departure.isoformat()])[:16],
                mode="transit",origin=origin,destination=destination,poi=poi,legs=legs,
                geometry={"type":"MultiLineString","coordinates":[l.geometry["coordinates"] for l in legs]},
                distance_m=sum(l.distance_m for l in legs),walking_m=sum(l.distance_m for l in walking),
                walking_s=sum(l.provider_duration_s for l in walking),stop_s=request.stop_duration_s,
                poi_arrival_time=arrival,poi_departure_time=departure,poi_leg_index=len(first.legs)-1,
                provider_total_s=total,total_s=total,evidence_ids=refs,
                source_warnings=list(dict.fromkeys([*first.source_warnings,*second.source_warnings])),
                time_basis="真实公共交通分段服务预计时间，含候车、公交／地铁换乘及已确认经停；非准时保证",
                fare_sgd=fare,fare_evidence_ids=transit_parts[0].fare_evidence_ids if fare is not None else [],
                fare_basis=transit_parts[0].fare_basis if len(transit_parts)==1 else
                    "经停前后分别查询公共交通，未核实一体化换乘计费；完整费用未知"))
    if not output:
        raise ToolFailure("此经停地点和出发时段未找到可衔接的公共交通方案。","no_route")
    return output


def transit_access(provider, origin, destination, request, poi=None, origin_hub=None, destination_hub=None):
    """Compose service foot access with time-dependent PT at discovered hubs."""
    from .models import Place
    prefixes=provider.routes(origin,origin_hub,"walking",False,1) if origin_hub else [None]
    routes=[]
    for prefix in prefixes:
        departure=request.departure_time
        start=origin
        if prefix:
            prefix=prefix.model_copy(deep=True)
            prefix.departure_time=departure
            departure+=timedelta(seconds=prefix.provider_duration_s)
            prefix.arrival_time=departure
            lon,lat=prefix.geometry["coordinates"][-1]
            start=Place(id=prefix.id+":access",name=origin_hub.name+" 路网接驳点",lon=lon,lat=lat,
                source="OSRM Route",evidence_ids=list(dict.fromkeys([*prefix.evidence_ids,*origin_hub.evidence_ids])))
            prefix.destination=start
        remaining=request.max_walking_m
        if remaining is not None:
            remaining-=prefix.distance_m if prefix else 0
            if remaining<0:
                continue
        onward=request.model_copy(update={"departure_time":departure,"max_walking_m":remaining})
        end=destination_hub or destination
        middles=transit_via(provider,start,end,poi,onward) if poi else provider.transit_routes(start,end,onward)
        for middle in middles[:3]:
            suffix=None
            if destination_hub:
                lon,lat=middle.legs[-1].geometry["coordinates"][-1]
                access=Place(id=middle.id+":exit",name=destination_hub.name+" 路网接驳点",lon=lon,lat=lat,
                    source="OneMap transit",evidence_ids=middle.evidence_ids)
                try:
                    suffix=provider.routes(access,destination,"walking",False,1)[0].model_copy(deep=True)
                except ToolFailure as error:
                    if error.code=="no_route":
                        continue
                    raise
                suffix.departure_time=middle.legs[-1].arrival_time
                suffix.arrival_time=suffix.departure_time+timedelta(seconds=suffix.provider_duration_s)
            legs=[*([prefix] if prefix else []),*middle.legs,*([suffix] if suffix else [])]
            walking=[l for l in legs if l.mode=="walking"]
            total=(legs[-1].arrival_time-request.departure_time).total_seconds()
            refs=list(dict.fromkeys([*middle.evidence_ids,*[e for l in legs for e in l.evidence_ids],
                *[e for h in (origin_hub,destination_hub) if h for e in h.evidence_ids]]))
            route=middle.model_copy(deep=True,update={"id":"transit-access:"+stable([l.id for l in legs]+[request.departure_time.isoformat()])[:16],
                "origin":origin,"destination":destination,"legs":legs,
                "geometry":{"type":"MultiLineString","coordinates":[l.geometry["coordinates"] for l in legs]},
                "provider_total_s":total,"total_s":total,"distance_m":sum(l.distance_m for l in legs),
                "walking_m":sum(l.distance_m for l in walking),"walking_s":sum(l.provider_duration_s for l in walking),
                "poi_leg_index":middle.poi_leg_index+(1 if prefix else 0) if middle.poi_leg_index is not None else None,
                "time_basis":"真实步行接驳＋公交／地铁服务估计，含候车、换乘与确认的经停；非准时保证","evidence_ids":refs})
            routes.append(route)
    if not routes:
        raise ToolFailure("此接驳站点组合没有满足步行预算的可衔接行程。","no_route")
    return routes
