"""Validation of model extraction and user corrections; no offline request parser."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from .models import TravelRequest, MAX_STOPS
from .tools import ToolFailure
SG = ZoneInfo("Asia/Singapore")
PROMPTS = {"origin":"从哪里出发？","destination":"要去哪里？","mode":"选择驾车、全程步行或驾车后步行接驳。",
    "departure_time":"什么时候出发？","stop_duration_s":"这次经停预计停留多少分钟？系统会自行查找地点；只经过可填 0。",
    "poi_name":"需要经停做什么，或是否有指定的地点？未指定门店时由系统查找。",
    "parking_duration_s":"为停车及转换交通方式预留多少分钟？请按自己的实际安排填写。",
    "multiple_stops":"目前最多支持 3 个途中经停点（按你说的顺序），请保留最多 3 个后重新提交。",
    "parking_place_id":"请选择实际使用的停车转换点。",
    "parking_access_confirmed":"是否确认所选停车点可以完成停车到步行的转换？",
    "travel_preferences":"请补充出行偏好或限制，例如是否可以驾车、是否愿意步行。",
    "car_access":"本次出行是否有可用车辆并愿意自己驾车？出租车和网约车费用不等于自驾费用。",
    "origin_access_confirmed":"是否确认已完成入境手续，并能从所选口岸位置开始行程？若实际在车站或乘车点，请重新填写该地点。",
    "origin_place_id":"请选择正确的起点。","destination_place_id":"请选择正确的终点。","poi_place_id":"请选择正确的业务经停地点。"}
USER_ONLY = {"origin_place_id","destination_place_id","poi_place_id","parking_place_id","parking_access_confirmed","travel_preferences","place_searches","origin_access_confirmed","departure_time_source"}

GENERIC_POIS = {"bakery":"bakery","bakeries":"bakery","面包店":"bakery","面包点":"bakery","面包房":"bakery",
    "cafe":"cafe","café":"cafe","coffee shop":"cafe","咖啡店":"cafe","咖啡馆":"cafe",
    "restaurant":"restaurant","餐厅":"restaurant","饭店":"restaurant","hospital":"hospital","医院":"hospital",
    "fuel":"amenity:fuel","加油站":"amenity:fuel","gas station":"amenity:fuel","petrol station":"amenity:fuel"}

# Phrases that request a travel family. Statements of ability or availability
# ("我有车", "可以自己开") are car_access evidence, not a mode request.
MODE_REQUESTS = {
    "driving":r"(?:开车|自驾|驾车)(?:去|到|过去|前往|回|从)|(?:想|要|打算|准备)(?:开车|自驾|驾车)|drive to|driving to|by car|I'll drive|I will drive",
    "walking":r"(?:走路|步行)(?:去|到|过去|前往|回|从)|全程走路|走过去|walk(?:ing)? to|on foot",
    "transit":r"(?:坐|搭|乘|乘坐|搭乘|用)(?:公交|巴士|地铁|捷运|轻轨|MRT|公共交通)|公共交通|public transport|by (?:bus|MRT|train)|take (?:the )?(?:bus|MRT|train)",
    "drive_walk":r"停车后?(?:再)?(?:走路|步行)|(?:开车|驾车|自驾).{0,8}(?:再)?(?:走路|步行)|park and walk|drive.{0,20}walk"}

# A clock time or calendar day, as opposed to a relative duration.
CN_NUMBER = r"[零〇一二两三四五六七八九十]{1,3}"
CLOCK_TIME = (r"\d{1,2}\s*[:：点时]|"+CN_NUMBER+r"\s*[点时]|[上下]午|早上|晚上|中午|凌晨|傍晚|今[天晚早]|明[天晚早]|后天|"
    r"\d{1,2}\s*(?:am|pm)\b|\bnoon\b|\bmidnight\b|\d{1,2}\s*月|\d{1,2}\s*[号日]|周[一二三四五六日天]|星期|"
    r"\b(?:today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b")

# Words that fix a clock time to a day half or calendar day.
DAY_PERIOD = (r"[上下]午|早上|早晨|晚上|夜里|半夜|午夜|中午|凌晨|傍晚|今[天晚早]|明[天晚早]|后天|\d{1,2}\s*月|\d{1,2}\s*[号日]|"
    r"周[一二三四五六日天]|星期|\b(?:am|pm|noon|midnight|today|tomorrow|tonight)\b|(?:1[3-9]|2[0-3])\s*[:：点时]")

# A relative time span ("45分钟内到达"), which is max_duration_s, not a clock deadline.
DURATION = (r"(?:\d+(?:\.\d+)?|"+CN_NUMBER+r"|半)\s*(?:个)?\s*(?:分钟|分|小时|钟头)|"
    r"\d+(?:\.\d+)?\s*(?:minutes?|mins?|hours?|hrs?)\b")
# An explicit "arrive before <clock time>" request that must not be dropped.
DEADLINE_REQUEST = (r"(?:\d{1,2}|"+CN_NUMBER+r")\s*(?:[点:：时]\s*(?:\d{1,2}|"+CN_NUMBER+r"|半)?\s*分?)\s*(?:前|之前|以前)\s*(?:到|抵达|赶到)|"
    r"before\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b|by\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b")

# "我可以选择开车去" offers an option; it is car_access, not a request to drive.
ABILITY_BEFORE = r"(?:可以|能够|能|可|会|也行|也可以)\s*(?:选择|考虑)?\s*$|\b(?:can|could|may|able to)\s*(?:choose to\s*)?$"

def mode_requested(mode,phrase):
    for match in re.finditer(MODE_REQUESTS[mode],phrase,re.I):
        if not re.search(ABILITY_BEFORE,phrase[max(0,match.start()-8):match.start()],re.I):
            return True
    return False

def validate_stops(request,text,sources,clarifications):
    """2-3 ordered business stops: each grounded in its own user excerpt."""
    if len(request.stops)<2:
        raise ToolFailure("只有一个经停点时请使用 poi_category/poi_name 与 stop_duration_s，stops 留空。","invalid_evidence")
    if request.poi_category or request.poi_name:
        raise ToolFailure("填写 stops 时，poi_category 与 poi_name 必须为 null；每个经停点的类别或名称写在各自的 stops 项中。","invalid_evidence")
    spans=[]
    for index,stop in enumerate(request.stops,1):
        if not stop.poi_category and not stop.poi_name:
            raise ToolFailure(f"第 {index} 个经停点缺少类别或名称：请按原文用途填写 OSM poi_category（如 amenity:post_office）。","invalid_evidence")
        generic=generic_poi_category(stop.poi_name)
        if generic or (stop.poi_category and stop.poi_name==stop.poi_category):
            raise ToolFailure(f"第 {index} 个经停点的名称是类别，不是指定门店：请设 poi_category={generic or stop.poi_category}、poi_name=null。","invalid_evidence")
        if not any(stop.evidence in source for source in sources):
            raise ToolFailure(f"第 {index} 个经停点的 evidence 必须是用户原文中的准确短语。","invalid_evidence")
        start=text.find(stop.evidence)
        if start>=0:
            spans.append((start,start+len(stop.evidence)))
        if stop.stop_duration_s is not None and "stops" not in clarifications:
            phrase=stop.duration_evidence or ""
            number=re.search(r"(\d+(?:\.\d+)?)",phrase)
            if not phrase or not any(phrase in source for source in sources) or not number:
                raise ToolFailure(f"第 {index} 个经停点的停留时间需要包含数字的原文依据 duration_evidence；用户没说就设为 null。","invalid_evidence")
            unit=1 if re.search(r"秒|seconds?",phrase,re.I) else 3600 if re.search(r"小时|hours?",phrase,re.I) else 60
            if abs(float(number[1])*unit-stop.stop_duration_s)>.01:
                raise ToolFailure(f"第 {index} 个经停点的停留时间与原文单位不一致。","invalid_evidence")
    if any(a<d and c<b for i,(a,b) in enumerate(spans) for c,d in spans[i+1:]):
        raise ToolFailure("多个经停的依据不能重复引用同一次经停，请区分真正不同的业务地点。","invalid_evidence")
    request.multiple_stops=True
    request.multiple_stop_evidence=[stop.evidence for stop in request.stops]

def generic_poi_category(name):
    return GENERIC_POIS.get((name or "").strip().casefold())

def validate_request(text, fields, clarifications):
    if set(fields)- (set(TravelRequest.model_fields)-{"original_text"}):
        raise ToolFailure("需求提取包含不支持的字段。", "invalid_arguments")
    values = dict(fields)
    for key,value in clarifications.items():
        if key in USER_ONLY:
            continue
        if key not in TravelRequest.model_fields or key in ("original_text","evidence"):
            raise ToolFailure("澄清字段不合法。", "invalid_arguments")
        if value != "":
            values[key] = value
    request = TravelRequest(original_text=text,**values)
    generic=generic_poi_category(request.poi_name)
    if generic or (request.poi_category and request.poi_name==request.poi_category):
        generic=generic or request.poi_category
        raise ToolFailure("类别名称不是指定门店：请将 poi_category 设为 "+generic+
            "、poi_name 设为 null，再通过 search_pois 自动搜索真实候选；不要要求用户提供门店地址。","invalid_evidence")
    for key in ("departure_time","arrival_deadline"):
        stamp = getattr(request,key)
        if stamp:
            setattr(request,key,stamp.replace(tzinfo=SG) if stamp.tzinfo is None else stamp.astimezone(SG))
    # Validate supporting phrases. These guards reject omitted/changed explicit
    # constraints; they do not create a fallback plan or parse absent values.
    sources = [text,*[value for value in clarifications.values() if isinstance(value,str)]]
    for field,phrase in request.evidence.items():
        if field in clarifications:
            continue  # Explicit user corrections have already replaced model values.
        if field not in TravelRequest.model_fields or not phrase or not any(phrase in source for source in sources):
            raise ToolFailure("需求依据必须是用户原文中的准确短语。", "invalid_evidence")
    for field in ("origin","destination"):
        if not getattr(request,field) and field not in clarifications and request.evidence.get(field):
            raise ToolFailure("已有"+field+"原文依据，不能把地点设为空；请保留用户描述并调用 resolve_place 查询，不要要求重新输入。","invalid_evidence")
    if request.stops:
        validate_stops(request,text,sources,clarifications)
    elif request.multiple_stops and "multiple_stops" not in clarifications:
        excerpts=list(dict.fromkeys(request.multiple_stop_evidence))
        if len(excerpts)<2 or any(not phrase.strip() or not any(phrase in source for source in sources) for phrase in excerpts):
            raise ToolFailure("多个业务经停必须提供至少两个不同经停地点的原文片段 multiple_stop_evidence；起终点不算经停，同一家店吃饭或购物只算一次。请修正需求提取。","invalid_evidence")
        spans=[]
        for phrase in excerpts:
            start=text.find(phrase)
            if start>=0:
                spans.append((start,start+len(phrase)))
        if any(a<d and c<b for i,(a,b) in enumerate(spans) for c,d in spans[i+1:]):
            raise ToolFailure("多个经停的依据不能重复引用同一次经停，请区分真正不同的业务地点。","invalid_evidence")
        if len(excerpts)<=MAX_STOPS:
            raise ToolFailure("用户给出了 "+str(len(excerpts))+" 个不同经停点：请按用户顺序填写 stops（每项含 poi_category 或 poi_name、"
                "evidence 原文，以及可选的 stop_duration_s 与 duration_evidence），并把 poi_category/poi_name 设为 null。","invalid_evidence")
    if request.car_access is not None and "car_access" not in clarifications:
        phrase=request.evidence.get("car_access","")
        negative=bool(re.search(r"没.*车|无车|不.*(?:开车|自驾|驾车)|no.*car|don.?t.*(?:car|driv)|cannot.*driv",phrase,re.I))
        positive=bool(re.search(r"有车|开车|自驾|驾车|own.*car|access.*car|can.*driv|will.*driv|driving",phrase,re.I))
        if not phrase or (request.car_access and (not positive or negative)) or (not request.car_access and not negative):
            raise ToolFailure("是否有可用车辆必须依据用户原文或用户确认，不能从赶时间推断。","invalid_evidence")
    if request.mode and "mode" not in clarifications:
        # The cited phrase, or failing that the user's own wording, must request this family.
        phrases=[request.evidence["mode"]] if request.evidence.get("mode") else sources
        if not any(mode_requested(request.mode,phrase) for phrase in phrases):
            raise ToolFailure("只有用户明确要求某种出行方式时才能填写 mode，且需要该要求的原文依据；"
                "「有车」「可以自己开」只表示 car_access，不是要求自驾。请把 mode 设为 null，由系统比较各方式。","invalid_evidence")
    if request.arrival_deadline and "arrival_deadline" not in clarifications:
        # "45分钟内到达" is a duration. Turning it into a clock time freezes the
        # extraction moment and silently shrinks the limit when departure is refreshed.
        phrase=request.evidence.get("arrival_deadline","")
        if phrase and re.search(DURATION,phrase,re.I) and not re.search(CLOCK_TIME,phrase,re.I):
            raise ToolFailure("「"+phrase+"」是相对时长，不是到达时刻：请填写 max_duration_s，并把 arrival_deadline 设为 null。"
                "若用户给出了具体时刻（如 12点前、十二点前），请保留 arrival_deadline 并引用该时刻原文。","invalid_evidence")
    for field in ("poi_required","avoid_highways","require_open","require_dry","arrival_deadline","max_congestion_fraction"):
        if getattr(request,field) and field not in clarifications and not request.evidence.get(field):
            raise ToolFailure("硬约束必须提供用户原文依据："+field,"invalid_evidence")
    if request.poi_required and not (request.poi_name or request.poi_category or request.stops):
        raise ToolFailure("经停需求尚未提取完整：请根据原文用途选择 poi_category 的 OSM 键和值（如 amenity:fuel），"
            "不要把未命名的经停任务当成缺少地址。仅原文未说明用途时向用户澄清。", "invalid_evidence")
    guards = {"avoid_highways":r"不能走高速|不能经过高速|必须避开高速|must avoid highways",
              "require_dry":r"保证.*不.*(?:淋雨|下雨)|必须不淋雨|must stay dry",
              "poi_required":r"必须经过|必须经停|must stop",
              "arrival_deadline":DEADLINE_REQUEST,
              "prefer_low_cost":r"经济实惠|最便宜|省钱|便宜|低成本|cheapest|affordable|low.cost",
              "prefer_fastest":r"最快|尽快|赶时间|fastest|as soon as possible",
              "weather_required":r"考虑天气|天气因素|consider.*weather"}
    for key,pattern in guards.items():
        if re.search(pattern,text,re.I) and key not in clarifications and not getattr(request,key):
            raise ToolFailure("提取遗漏了用户明确的要求或偏好："+key,"invalid_evidence")
    for field in ("max_duration_s","stop_duration_s","max_walking_m","parking_duration_s"):
        if field in clarifications or getattr(request,field) is None:
            continue
        phrase = request.evidence.get(field)
        if not phrase:
            raise ToolFailure("数值约束必须提供原文依据："+field,"invalid_evidence")
        number = re.search(r"(\d+(?:\.\d+)?)",phrase)
        if not number:
            raise ToolFailure("数值约束依据缺少明确数字，请澄清。","invalid_evidence")
        unit = 1 if field=="max_walking_m" or re.search(r"秒|seconds?",phrase,re.I) else 3600 if re.search(r"小时|hours?",phrase,re.I) else 60
        if field=="max_walking_m" and re.search(r"公里|kilomet|\bkm\b",phrase,re.I):
            unit = 1000
        if abs(float(number[1])*unit-getattr(request,field))>.01:
            raise ToolFailure("提取数值与原文单位不一致："+field,"invalid_evidence")
    if request.departure_time is None:
        request.departure_time = datetime.now(SG)
    questions = {f:PROMPTS[f] for f in ("origin","destination") if getattr(request,f) is None}
    if (request.poi_category or request.poi_name) and request.stop_duration_s is None:
        questions["stop_duration_s"] = PROMPTS["stop_duration_s"]
    if request.stops and request.stop_duration_s is None and any(stop.stop_duration_s is None for stop in request.stops):
        missing="、".join("「"+stop.evidence+"」" for stop in request.stops if stop.stop_duration_s is None)
        questions["stop_duration_s"] = "以下经停点每处预计停留多少分钟？"+missing+"（用于每个未说明停留时间的经停点）"
    if request.mode=="drive_walk" and request.parking_duration_s is None:
        questions["parking_duration_s"] = PROMPTS["parking_duration_s"]
    if request.multiple_stops and not request.stops:
        questions["multiple_stops"] = PROMPTS["multiple_stops"]
    if request.arrival_deadline and request.departure_time and request.arrival_deadline<=request.departure_time:
        questions["arrival_deadline"] = "到达时限不晚于出发时间，请明确到达日期和时间。"
    elif (request.arrival_deadline and request.departure_time and "arrival_deadline" not in clarifications and
            (request.arrival_deadline-request.departure_time).total_seconds()>12*3600 and
            not re.search(DAY_PERIOD,request.evidence.get("arrival_deadline",""),re.I)):
        # "十二点前" at 23:00 may mean midnight or tomorrow noon; a 12-hour clock
        # reading without 上午/下午/明天 is the user's fact to confirm, not the model's.
        questions["arrival_deadline"] = ("「"+request.evidence.get("arrival_deadline","到达时间")+"」可以理解为不同时刻，"
            "已按 "+request.arrival_deadline.astimezone(SG).strftime("%m-%d %H:%M")+" 填入，请确认或修改到达时限。")
    return request,questions
