"""Assemble only real provider legs; no invented connections or local speed guesses."""
from .models import RouteCandidate
from .providers import stable
def assemble(legs, request, origin, destination, poi=None, parking=None):
    driving = [x for x in legs if x.mode=="driving"]
    walking = [x for x in legs if x.mode=="walking"]
    drive_distance = sum(x.distance_m for x in driving)
    coverage = sum(x.distance_m*(x.traffic_coverage or 0) for x in driving)/drive_distance if drive_distance else None
    covered = all(x.traffic_duration_s is not None for x in driving)
    parking_s = request.parking_duration_s if parking else 0
    stop_s = (request.stop_duration_s or 0) if poi else 0
    provider_total = sum(x.provider_duration_s for x in legs)+stop_s+parking_s if parking_s is not None else None
    total = sum(x.traffic_duration_s if x.mode=="driving" else x.provider_duration_s for x in legs)+stop_s+parking_s if covered and parking_s is not None else None
    known_length = sum(x.distance_m*(x.traffic_coverage or 0) for x in driving)
    congestion = sum(x.distance_m*(x.traffic_coverage or 0)*(x.congestion_fraction or 0) for x in driving)/known_length if known_length else None
    refs = list(dict.fromkeys([e for leg in legs for e in leg.evidence_ids]+
                (poi.evidence_ids if poi else [])+(parking.evidence_ids if parking else [])))
    return RouteCandidate(id="route-"+stable([x.id for x in legs])[:16],mode=request.mode,origin=origin,
        destination=destination,legs=[x.model_copy(deep=True) for x in legs],poi=poi,parking=parking,
        geometry={"type":"Feature","properties":{},"geometry":{"type":"MultiLineString",
            "coordinates":[x.geometry["coordinates"] for x in legs]}},
        distance_m=sum(x.distance_m for x in legs),driving_s=sum(x.provider_duration_s for x in driving),
        walking_s=sum(x.provider_duration_s for x in walking),walking_m=sum(x.distance_m for x in walking),
        stop_s=stop_s,parking_s=parking_s,provider_total_s=provider_total,total_s=total,
        time_basis="LTA 覆盖下的驾车估计＋OSRM 步行估计＋用户预留时间" if driving and total is not None else
            "OSRM 步行服务估计" if not driving else "OSRM 基础时间，非实时交通时间",
        traffic_coverage=coverage,congestion_fraction=congestion,evidence_ids=refs)
