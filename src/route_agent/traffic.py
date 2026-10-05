"""LTA matching to actual OSRM route geometry, without filling uncovered roads."""
import math
from datetime import datetime, timezone, timedelta
from shapely.geometry import LineString
from shapely.strtree import STRtree
from pyproj import Transformer
from .providers import distance
PROJECT = Transformer.from_crs(4326,32648,always_xy=True).transform
def future_departure(departure, reference=None):
    """Live observations cannot describe a scheduled future trip."""
    return bool(departure and departure > (reference or datetime.now(timezone.utc))+timedelta(seconds=60))

def past_departure(departure, reference=None, max_age=600):
    """Today's live observations are not historical traffic evidence."""
    return bool(departure and departure < (reference or datetime.now(timezone.utc))-timedelta(seconds=max_age))

def heading(a,b):
    return math.degrees(math.atan2(b[0]-a[0],b[1]-a[1])) % 360
def prepare_bands(records):
    lines, values = [], []
    for item in records:
        try:
            nums = [float(v) for v in item["Location"].split()] if "Location" in item else [
                float(item[k]) for k in ("StartLat","StartLon","EndLat","EndLon")]
            if len(nums) < 4 or len(nums)%2:
                continue
            coords = [(nums[i+1],nums[i]) for i in range(0,len(nums),2)]
            lo, hi = float(item["MinimumSpeed"]),float(item["MaximumSpeed"])
            if not 0 <= lo <= hi or hi <= 0:
                continue
            shape = LineString([PROJECT(*p) for p in coords])
            if shape.length == 0:
                continue
            lines.append(shape)
            values.append({"low":lo,"high":hi,"heading":heading(coords[0],coords[-1]),
                           "id":str(item.get("LinkID","")),"name":str(item.get("RoadName","")).casefold()})
        except (KeyError,ValueError,TypeError):
            continue
    return lines, values, STRtree(lines) if lines else None

def apply_traffic(legs, payload, departure, max_age=600, budget=None, prepared=None):
    stamp = datetime.fromisoformat(payload["retrieved_at"])
    current = datetime.now(timezone.utc)
    # A one-minute tolerance only covers immediate submission/clock skew;
    # current conditions cannot certify a scheduled future departure.
    fresh = abs((departure-stamp).total_seconds()) <= max_age and (current-stamp).total_seconds() <= max_age and departure<=current+timedelta(seconds=60)
    shapes, bands, tree = (prepared if prepared is not None else prepare_bands(payload.get("speed_bands",[]))) if fresh else ([],[],None)
    for leg in legs:
        if leg.mode != "driving":
            continue
        coords = leg.geometry["coordinates"]
        total_geom = sum(distance(a,b) for a,b in zip(coords,coords[1:]))
        scale = leg.distance_m/total_geom if total_geom else 0
        known = middle = lower = upper = congested = 0.0
        upper_known = True
        for index,(a,b) in enumerate(zip(coords,coords[1:])):
            if budget and index%50==0:
                budget.check()
            length = distance(a,b)
            parts = max(1,math.ceil(length/20))
            name = next((s["name"].casefold() for s in leg.segments if s["start"]<=index<s["end"]),"")
            for part in range(parts):
                start = [a[i]+(b[i]-a[i])*part/parts for i in (0,1)]
                end = [a[i]+(b[i]-a[i])*(part+1)/parts for i in (0,1)]
                line = LineString([PROJECT(*start),PROJECT(*end)])
                candidates = []
                if tree is not None and line.length > 0:
                    for j in tree.query(line.buffer(25)):
                        band = bands[int(j)]
                        angle = abs((heading(start,end)-band["heading"]+180)%360-180)
                        overlap = line.intersection(shapes[int(j)].buffer(25)).length/line.length
                        if angle<=30 and overlap>=.8 and (not name or name=="-" or not band["name"] or
                                name in band["name"] or band["name"] in name):
                            candidates.append((overlap-angle/180,band))
                if not candidates:
                    continue
                band = max(candidates,key=lambda x:x[0])[1]
                metres = length/parts*scale
                known += metres
                middle += metres/(((band["low"]+band["high"])/2)/3.6)
                lower += metres/(band["high"]/3.6)
                if band["low"]>0:
                    upper += metres/(band["low"]/3.6)
                else:
                    upper_known = False
                # Congestion rule is explicit and uses the API's base route estimate.
                base_speed = leg.distance_m/max(leg.provider_duration_s,.001)*3.6
                if (band["low"]+band["high"])/2 < base_speed/1.3:
                    congested += metres
        leg.traffic_coverage = min(1,known/leg.distance_m) if leg.distance_m else None
        covered = leg.traffic_coverage is not None and leg.traffic_coverage>=1-1e-6
        leg.traffic_duration_s = middle if covered else None
        leg.partial_traffic_duration_s = middle+leg.provider_duration_s*(1-leg.traffic_coverage) if known and leg.traffic_coverage is not None else None
        leg.traffic_lower_s = lower if covered else None
        leg.traffic_upper_s = upper if covered and upper_known else None
        leg.congestion_fraction = congested/known if known else None
        leg.traffic_retrieved_at = stamp
        leg.evidence_ids = list(dict.fromkeys([*leg.evidence_ids,*payload.get("evidence_ids",[])]))
