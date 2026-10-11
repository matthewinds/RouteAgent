"""Official card-fare service with verified transfer boundaries and operator tariffs.

Distances/prices come from services, not route geometry or model estimates.
LTA's calculator is a website backend, not a versioned public DataMall API;
schema or coverage changes leave an explicit gap, never a guessed total.
"""
import re
import math
from decimal import Decimal
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

from .tools import ToolFailure

LTA_SITE = "https://www.lta.gov.sg"
LTA_CALCULATOR = LTA_SITE + "/content/ltagov/en/map/fare-calculator.html"
LTA_ENDPOINT = LTA_SITE + "/content/ltagov/en/map/fare-calculator/jcr:content/map2-content/farecalculator"
TRANSFER_RULES = "https://www.ptc.gov.sg/fares/distance-fares-and-transfer-rules/"
OPERATOR_TARIFF = "https://b2b.sentosa.com.sg/faq"
ZERO_STATE = "-".join([f"usiAccumulatedDistance{i}=0" for i in range(1,7)] +
                       [f"usiAccumulatedFare{i}=0" for i in range(1,7)])
PROFILE = "成人交通卡标准票价参考；不含个人／时段优惠、通票或支付附加费"


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style"):
            self.hidden += 1
    def handle_endtag(self,tag):
        if tag in ("script","style"):
            self.hidden = max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(html):
    parser=PageText();parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def read_xml(provider,kind):
    payload,ev=provider.request("GET",LTA_SITE+f"/map/fareCalculator/{kind}TripIndex.xml",
        "LTA fare station catalogue",ttl=86400,response_format="text")
    try:
        root=ET.fromstring(payload["text"])
        if root.tag != {"mrt":"mrtTripFareCalc","bus":"busTripFareCalc"}[kind]:
            raise ValueError()
        return root,ev
    except (ET.ParseError,KeyError,ValueError,TypeError):
        raise ToolFailure("LTA 票价站点目录无效，未猜测站点编号。","invalid_response") from None


def rail_stations(root):
    stations={}
    for stop in root.findall(".//mrtStop"):
        value=stop.get("value","").split("_")[0]
        if not value.isdigit():
            continue
        aliases=re.search(r"\(([^)]+)\)\s*$",stop.text or "")
        for code in re.findall(r"\b[A-Z]{1,4}\d{0,3}\b",aliases[1] if aliases else ""):
            if code in stations and stations[code]!=value:
                raise ToolFailure("LTA 票价站点编号存在歧义。","invalid_response")
            stations[code]=value
    return stations


def bus_pair(root,leg):
    service=leg.service_name or ""
    options=set()
    for bus in root.findall(".//bus"):
        if bus.get("id")!=service:
            continue
        for direction in bus.findall("direction"):
            stops=[]
            for stop in direction.findall("busStop"):
                parts=stop.get("id","").split("_")
                if len(parts)==3 and all(part.isdigit() for part in parts):
                    stops.append(parts)
            for i,a in enumerate(stops):
                if a[1]!=leg.origin.stop_code:
                    continue
                for b in stops[i+1:]:
                    if b[1]==leg.destination.stop_code:
                        options.add((a[0],b[0]))
    if len(options)!=1:
        raise ToolFailure("LTA 目录未唯一核实此公交线路及上下车站，票价未知。","fare_coverage")
    return next(iter(options))


def transfer_rules(provider):
    payload,ev=provider.request("GET",TRANSFER_RULES,"PTC transfer rules",ttl=3600,response_format="text")
    text=plain_text(payload.get("text","")).lower()
    numbers={"two":2,"five":5}
    def number(pattern):
        match=re.search(pattern,text)
        if not match:
            raise ToolFailure("官方换乘规则结构变化，未核实整程计费。","fare_coverage")
        raw=match[1]
        return int(raw) if raw.isdigit() else numbers[raw]
    rules={"max_boarding_span_s":number(r"within a journey is (\d+|two) hours")*3600,
        "max_transfers":number(r"maximum of (\d+|five) transfers"),
        "mixed_gap_s":number(r"(\d+) minutes for transfers between train station and bus service")*60,
        "rail_gap_s":number(r"(\d+) minutes for transfers between different train stations")*60}
    if "same number as the preceding bus service" not in text or "no exit and re-entry at the same train station" not in text:
        raise ToolFailure("官方换乘限制未完整核实，未生成合计票价。","fare_coverage")
    return rules,ev


def valid_transfer(previous,current,first_boarding,count,rules):
    if previous["kind"]=="operator" or current["kind"]=="operator":
        return False,"独立运营线路单独计费"
    gap=(current["departure"]-previous["arrival"]).total_seconds()
    if gap<0:
        raise ToolFailure("票价行程的换乘时序无效。","invalid_response")
    limit=rules["rail_gap_s"] if previous["kind"]==current["kind"]=="mrt" else rules["mixed_gap_s"]
    if gap>limit:
        return False,"上下车之间的步行、等待及停留超过官方换乘时限"
    if (current["departure"]-first_boarding).total_seconds()>rules["max_boarding_span_s"]:
        return False,"超过整段计费行程的首末上车时限"
    if count>rules["max_transfers"]:
        return False,"超过官方最大换乘次数"
    if previous["kind"]==current["kind"]=="bus" and re.match(r"\d+",previous["service"]).group()==re.match(r"\d+",current["service"]).group():
        return False,"同号或附属公交服务按新行程计费"
    if previous["kind"]==current["kind"]=="mrt" and previous["to"]==current["from"]:
        return False,"经停后离站并重新进入同一车站，按新行程计费"
    return True,"符合已核实的官方换乘规则"


def rides_for(route,stations,buses):
    rides=[]
    for i,leg in enumerate(route.legs):
        if leg.mode not in ("bus","rail"):
            continue
        if leg.source!="OneMap transit" or not leg.departure_time or not leg.arrival_time or leg.arrival_time<leg.departure_time:
            raise ToolFailure("缺少真实公共交通站点及时序，未计算票价。","invalid_evidence")
        codes=(leg.origin.stop_code,leg.destination.stop_code)
        if leg.mode=="rail" and leg.service_name=="SS":
            if not all(code and re.fullmatch(r"S[1-4]",code) for code in codes):
                raise ToolFailure("独立运营线路的上下车站未核实。","fare_coverage")
            ride={"kind":"operator","from":codes[0],"to":codes[1],"service":"SS"}
        elif leg.mode=="rail":
            if any(code not in stations for code in codes):
                raise ToolFailure("LTA 未覆盖此轨道线路的真实站点，票价未知。","fare_coverage")
            ride={"kind":"mrt","from":stations[codes[0]],"to":stations[codes[1]],"service":leg.service_name}
        else:
            if not re.fullmatch(r"\d+[A-Za-z]*",leg.service_name or ""):
                raise ToolFailure("此公交服务不在已核实的官方票价覆盖范围。","fare_coverage")
            a,b=bus_pair(buses,leg)
            ride={"kind":"bus","from":a,"to":b,"service":leg.service_name}
        ride.update(departure=leg.departure_time,arrival=leg.arrival_time,leg_ids=[leg.id],
            boarding=leg.origin.name,alighting=leg.destination.name,start_index=i,end_index=i)
        if rides:
            prev=rides[-1]
            if prev["arrival"]>ride["departure"]:
                raise ToolFailure("票价分段的服务时序冲突。","invalid_response")
            stop_indices=[*([route.poi_leg_index] if route.poi_leg_index is not None else []),*route.stop_leg_indices]
            business_exit=any(prev["end_index"]<=index<i for index in stop_indices)
            # Consecutive rail legs in a service itinerary are one paid-area
            # ride when the official station catalogue proves the interchange.
            if not business_exit and prev["kind"]==ride["kind"]=="mrt" and prev["to"]==ride["from"]:
                prev.update(to=ride["to"],arrival=ride["arrival"],alighting=ride["alighting"],end_index=i,
                    leg_ids=prev["leg_ids"]+ride["leg_ids"],
                    service=" / ".join(dict.fromkeys([*(prev["service"] or "MRT/LRT").split(" / "),ride["service"] or "MRT/LRT"])))
                continue
        rides.append(ride)
    return rides


def operator_fare(provider,ride):
    payload,ev=provider.request("GET",OPERATOR_TARIFF,"Official operator tariff",ttl=3600,response_format="text")
    text=plain_text(payload.get("text",""))
    match=re.search(r"Sentosa Express\s*-\s*Standard Fee per guest:\s*\$\s*(\d+(?:\.\d{1,2})?)",text,re.I)
    if not match:
        raise ToolFailure("官方独立运营线路票价未取得，未填入固定金额。","fare_coverage")
    if ride["from"]=="S1":
        return Decimal(match[1]),ev,"从 VivoCity 上车的标准入岛费（非公交／地铁换乘票价）"
    # Only on-island trips are covered by this source's explicit free rule.
    if ride["to"]!="S1" and re.search(r"Once on the island, you can travel at no cost.*?Sentosa Express",text,re.I):
        return Decimal(0),ev,"官方明确的岛内免费乘坐"
    raise ToolFailure("此方向的独立运营线路收费规则未核实。","fare_coverage")


def calculate_fare(provider,route):
    """One exact itinerary; all public legs must be priced to set a total."""
    refs=[];components=[];total=Decimal(0)
    try:
        public=[leg for leg in route.legs if leg.mode in ("bus","rail")]
        stations,buses={},None
        if any(leg.mode=="rail" and leg.service_name!="SS" for leg in public):
            root,ev=read_xml(provider,"mrt");stations=rail_stations(root);refs.append(ev)
        if any(leg.mode=="bus" for leg in public):
            buses,ev=read_xml(provider,"bus");refs.append(ev)
        rides=rides_for(route,stations,buses)
        if not rides:
            raise ToolFailure("没有可核实的公共交通分段。","invalid_evidence")
        rules={}
        if any(a["kind"]!="operator" and b["kind"]!="operator" for a,b in zip(rides,rides[1:])):
            rules,ev=transfer_rules(provider);refs.append(ev)
        state,additional=ZERO_STATE,"0"
        previous=None;first_boarding=None;count=0;group=0
        for ride in rides:
            reset,reason=True,"首次上车"
            if previous:
                allowed,reason=valid_transfer(previous,ride,first_boarding,count+1,rules)
                reset=not allowed
            if reset:
                state,additional=ZERO_STATE,"0"
                first_boarding=ride["departure"];count=0;group+=1
            else:
                count+=1
            component={"leg_ids":ride["leg_ids"],"service":ride["service"],"boarding":ride["boarding"],
                "alighting":ride["alighting"],"journey_group":group,"transfer_basis":reason,
                "fare_sgd":None,"evidence_ids":[],"source_url":OPERATOR_TARIFF if ride["kind"]=="operator" else LTA_CALCULATOR}
            components.append(component)
            if ride["kind"]=="operator":
                amount,ev,basis=operator_fare(provider,ride)
                component["basis"]=basis
            else:
                body={"fare":"30","from":ride["from"],"to":ride["to"],"tripInfo":state,"addTripInfo":additional}
                if ride["kind"]=="bus":
                    body["bus"]=ride["service"]
                payload,ev=provider.request("POST",LTA_ENDPOINT+f'.{ride["kind"]}get.html',"LTA fare calculator",
                    data=body,ttl=300)
                if not isinstance(payload,dict) or any(not isinstance(payload.get(k),str) or
                        not re.fullmatch(r"\d+",payload[k]) for k in ("fare","distance")) or int(payload["distance"])<=0:
                    raise ToolFailure("LTA 未返回有效站点票价，未生成替代金额。","invalid_response")
                state,additional=payload.get("tripInfo"),payload.get("addTripInfo")
                if not isinstance(state,str) or not isinstance(additional,str) or not state or not additional or max(len(state),len(additional))>8192:
                    raise ToolFailure("LTA 换乘计费状态缺失，未将独立票价冒充整程票价。","invalid_response")
                amount=Decimal(payload["fare"])/100
                component.update(fare_distance_m=int(payload["distance"])*10,basis="LTA 服务返回的本次上车应计金额，保留累计换乘状态")
            if not amount.is_finite() or not math.isfinite(float(amount)):
                raise ToolFailure("官方来源返回的票价不是有效有限金额。","invalid_response")
            component.update(fare_sgd=float(amount),evidence_ids=[ev])
            refs.append(ev);total+=amount;previous=ride
        route.fare_sgd=float(total)
        route.fare_basis="按实际上下车站点及经停换乘规则核算的官方票价参考。"+PROFILE
        error=None
    except ToolFailure as failure:
        route.fare_sgd=None
        error={"code":failure.code,"message":str(failure)}
        route.fare_basis="整程票价尚未完整核实："+str(failure)
    route.fare_components=components
    route.fare_known_subtotal_sgd=float(total) if any(c["fare_sgd"] is not None for c in components) else None
    route.fare_profile=PROFILE
    route.fare_evidence_ids=list(dict.fromkeys(refs))
    route.evidence_ids=list(dict.fromkeys([*route.evidence_ids,*refs]))
    return {"id":route.id,"fare_sgd":route.fare_sgd,"known_subtotal_sgd":route.fare_known_subtotal_sgd,
        "fare_basis":route.fare_basis,"components":components,"evidence_ids":route.fare_evidence_ids,"error":error}
