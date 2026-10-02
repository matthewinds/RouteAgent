"""Validation of model extraction and user corrections; no offline request parser."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from .models import TravelRequest
from .tools import ToolFailure
SG = ZoneInfo("Asia/Singapore")
PROMPTS = {"origin":"从哪里出发？","destination":"要去哪里？","mode":"选择驾车、全程步行或驾车后步行接驳。",
    "departure_time":"什么时候出发？","stop_duration_s":"业务经停预计停留多少分钟？只经过可填 0。",
    "parking_duration_s":"为停车及转换交通方式预留多少分钟？请按自己的实际安排填写。",
    "multiple_stops":"目前只支持一个业务经停点，请修改需求或明确仅保留一个。",
    "parking_place_id":"请选择实际使用的停车转换点。",
    "parking_access_confirmed":"是否确认所选停车点可以完成停车到步行的转换？",
    "travel_preferences":"请补充出行偏好或限制，例如是否可以驾车、是否愿意步行。",
    "origin_place_id":"请选择正确的起点。","destination_place_id":"请选择正确的终点。","poi_place_id":"请选择正确的业务经停地点。"}
USER_ONLY = {"origin_place_id","destination_place_id","poi_place_id","parking_place_id","parking_access_confirmed","travel_preferences"}

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
    for field in ("poi_required","avoid_highways","require_open","require_dry","arrival_deadline","max_congestion_fraction"):
        if getattr(request,field) and field not in clarifications and not request.evidence.get(field):
            raise ToolFailure("硬约束必须提供用户原文依据："+field,"invalid_evidence")
    guards = {"avoid_highways":r"不能走高速|不能经过高速|必须避开高速|must avoid highways",
              "require_dry":r"保证.*不.*(?:淋雨|下雨)|必须不淋雨|must stay dry",
              "poi_required":r"必须经过|必须经停|must stop"}
    for key,pattern in guards.items():
        if re.search(pattern,text,re.I) and key not in clarifications and not getattr(request,key):
            raise ToolFailure("提取遗漏了用户明确的硬约束："+key,"invalid_evidence")
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
    if request.mode=="drive_walk" and request.parking_duration_s is None:
        questions["parking_duration_s"] = PROMPTS["parking_duration_s"]
    if request.poi_required and not (request.poi_name or request.poi_category):
        questions["poi_name"] = "必须经停哪个地点或哪类店铺？"
    if request.multiple_stops:
        questions["multiple_stops"] = PROMPTS["multiple_stops"]
    if request.arrival_deadline and request.departure_time and request.arrival_deadline<=request.departure_time:
        questions["arrival_deadline"] = "到达时限不晚于出发时间，请明确到达日期和时间。"
    return request,questions
