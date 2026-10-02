"""Validated request parsing. Rules are the offline fallback, not a learned model."""
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any
from .models import TravelRequest
from .config import Settings

SG = ZoneInfo("Asia/Singapore")
PARSER_PROMPT = """Extract a Singapore travel request as one JSON object. Do not invent missing information.
Use this exact JSON structure and these value types:
{"origin":null,"destination":null,"mode":null,"max_duration_s":null,
"poi_category":null,"poi_name":null,"poi_required":false,"stop_duration_s":null,
"avoid_highways":false,"prefer_avoid_highways":false,"prefer_avoid_congestion":false,
"max_congestion_fraction":null,"require_open":false,"max_walking_m":null,
"weather_required":false,"evidence":{}}
origin/destination/poi_name are strings or JSON null. mode is driving/walking/transit or null.
poi_category is cafe/restaurant/hospital or null. Numeric units are seconds and meters.
All boolean fields MUST be true or false, never strings such as hard or soft.
All absent values MUST be JSON null, never the string "null". Evidence MUST be an object mapping
field names to exact supporting phrases from the input, not a paragraph.
Must/cannot/at most are hard constraints: set avoid_highways for required highway avoidance,
max_congestion_fraction=0 for mandatory congestion avoidance. Prefer/try/hope are soft:
set the corresponding prefer_ field. Absent preferences are false.
A requested stop is required unless explicitly optional. Missing mode/stop duration remain null.
Do not output departure_time or arrival_deadline; those are validated separately.
"""


def _rules(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {"evidence": {}}
    ending = r"(?=[，,。；;]|\s+(?:within|in\s+\d|by\s+\d)|\s*\d+\s*分钟|$)"
    match = re.search(r"从\s*(.+?)\s*到\s*(.+?)" + ending, text)
    if not match:
        match = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)" + ending, text, re.I)
    if match:
        values.update(origin=match[1].strip(), destination=match[2].strip())
        values["evidence"]["origin_destination"] = match[0]
    for mode, pattern in [("driving", r"驾车|开车|自驾|driv(?:e|ing)|by car"),
                          ("walking", r"步行出发|步行从|全程步行|walk(?:ing)? from|on foot"),
                          ("transit", r"公共交通|公交|地铁|bus|metro|transit")]:
        found = re.search(pattern, text, re.I)
        if found:
            values["mode"] = mode
            values["evidence"]["mode"] = found[0]
            break
    time = re.search(r"(\d+(?:\.\d+)?)\s*分钟(?:内|以内)|(?:within|at most|no more than)\s+(\d+(?:\.\d+)?)\s*(?:minutes?|mins?)", text, re.I)
    if time:
        values["max_duration_s"] = float(time[1] or time[2]) * 60
        values["evidence"]["max_duration_s"] = time[0]
    stop = re.search(r"(?:停留|停靠|停|用餐)\s*(\d+(?:\.\d+)?)\s*分钟|(?:stop for|spend)\s+(\d+(?:\.\d+)?)\s*(?:minutes?|mins?)", text, re.I)
    if stop:
        values["stop_duration_s"] = float(stop[1] or stop[2]) * 60
        values["evidence"]["stop_duration_s"] = stop[0]
    for category, pattern in [("cafe", r"咖啡店|咖啡馆|café|cafe|coffee shop"),
                              ("restaurant", r"餐厅|restaurant"), ("hospital", r"医院|hospital")]:
        poi = re.search(pattern, text, re.I)
        if poi:
            values["poi_category"] = category
            before = text[max(0, poi.start()-16):poi.start()]
            values["poi_required"] = not bool(re.search(r"希望|尽量|最好|prefer|optional", before, re.I)) or "必须" in before
            values["evidence"]["poi"] = text[max(0, poi.start()-10):poi.end()]
            break
    named = re.search(r"(?:必须经过|经停|stop at)\s*[「『\"](.+?)[」』\"]", text, re.I)
    if named:
        values["poi_name"] = named[1]
        values["poi_required"] = True
        values["evidence"]["poi_name"] = named[0]
    values["prefer_avoid_congestion"] = bool(re.search(r"避堵|避开拥堵|avoid congestion|less congestion", text, re.I))
    if re.search(r"必须避开拥堵|不能经过拥堵|must avoid congestion", text, re.I):
        values["max_congestion_fraction"] = 0.0
    highways = re.search(r"高速|expressway|highway", text, re.I)
    if highways:
        before = text[max(0, highways.start()-20):highways.start()]
        if re.search(r"避开|不要|不能|禁止|avoid|no ", before, re.I):
            soft = bool(re.search(r"尽量|希望|prefer|try", before, re.I)) and not bool(re.search(r"必须|不能|must", before, re.I))
            values["avoid_highways"] = not soft
            values["prefer_avoid_highways"] = soft
    values["require_open"] = bool(re.search(r"必须营业|营业中|开门|must be open|open cafe", text, re.I))
    walk = re.search(r"(?:步行(?:距离)?(?:不超过|最多|上限)|walking(?: distance)?(?: at most| limit)?)\s*(\d+)\s*(?:米|m\b|meters?)", text, re.I)
    if walk:
        values["max_walking_m"] = float(walk[1])
    values["weather_required"] = bool(re.search(r"下雨|雨中|天气|rain|weather|shelter", text, re.I))
    return values


def parse_request(text, clarifications=None, *, settings=None, now=None):
    settings = settings or Settings()
    now = now or datetime.now(SG)
    text = text.strip()
    values = _rules(text)
    parser_used = "rules"
    warnings = []
    usage = {}
    if settings.parser == "llm":
        try:
            from openai import OpenAI
            import httpx
            key = os.getenv("OPENROUTER_API_KEY")
            if not key:
                raise ValueError("Missing model credential")
            with OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1", timeout=30,
                        max_retries=0, http_client=httpx.Client(trust_env=False)) as client:
                reply = client.chat.completions.create(model=settings.model, temperature=0,
                    messages=[{"role": "system", "content": PARSER_PROMPT}, {"role": "user", "content": text}],
                    response_format={"type": "json_object"}, max_tokens=1200)
                extracted = json.loads(reply.choices[0].message.content or "{}")
                TravelRequest(original_text=text, **extracted)  # reject unknown/invalid keys
                values = extracted
                parser_used = "llm"
                usage = reply.usage.model_dump() if reply.usage else {}
        except Exception as error:
            warnings.append(f"需求模型不可用，已使用规则解析（{type(error).__name__}）。")
    # Scope checks are deterministic even when the language model omits a stop.
    categories = sum(bool(re.search(pattern, text, re.I)) for pattern in
        (r"咖啡店|咖啡馆|café|cafe|coffee shop", r"餐厅|restaurant", r"医院|hospital"))
    named_stops = re.search(r"(?:必须经过|经停|stop at)(.+)", text, re.I)
    values["multiple_stops"] = categories > 1 or bool(re.search(r"(?:两个|两家|多个|two|multiple)\s*(?:经停|地点|店|stops?)", text, re.I)) or bool(
        named_stops and len(re.findall(r'[「『"](.+?)[」』"]', named_stops[1])) > 1)
    permitted = set(TravelRequest.model_fields) - {"original_text", "evidence"}
    for name, value in (clarifications or {}).items():
        if name not in permitted:
            raise ValueError(f"Unknown clarification field: {name}")
        if value != "":
            values[name] = value
    if values.get("departure_time"):
        dep = values["departure_time"]
        dep = datetime.fromisoformat(dep) if isinstance(dep, str) else dep
        values["departure_time"] = dep.replace(tzinfo=SG) if dep.tzinfo is None else dep.astimezone(SG)
    elif re.search(r"现在|立即|\bnow\b", text, re.I):
        values["departure_time"] = now
    deadline = re.search(r"(?:在)?(\d{1,2}):(\d{2})\s*(?:前到|前抵达)|\bby\s+(\d{1,2}):(\d{2})", text, re.I)
    if deadline and not (clarifications or {}).get("arrival_deadline"):
        hour, minute = int(deadline[1] or deadline[3]), int(deadline[2] or deadline[4])
        base = values.get("departure_time") or now
        if hour > 23 or minute > 59:
            raise ValueError("Invalid arrival deadline")
        values["arrival_deadline"] = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    request = TravelRequest(original_text=text, **values)
    questions = {}
    for field, prompt in [("origin", "从哪里出发？"), ("destination", "要去哪里？"),
                          ("mode", "使用什么交通方式？首版支持驾车。"),
                          ("departure_time", "什么时候出发？")]:
        if getattr(request, field) is None:
            questions[field] = prompt
    if (request.poi_category or request.poi_name) and request.stop_duration_s is None:
        questions["stop_duration_s"] = "经停地点预计停留多久？请输入分钟；只经过可填 0。"
    if request.mode and request.mode != "driving":
        questions["mode"] = "首版只支持驾车，请选择驾车或修改请求。"
    if request.weather_required:
        questions["weather_required"] = "首版未实现天气约束；请明确取消该要求后继续。"
    if request.max_walking_m is not None:
        questions["max_walking_m"] = "首版不核验步行接驳距离；请明确取消步行上限后继续。"
    if request.multiple_stops:
        questions["multiple_stops"] = "首版只支持一个经停点；请确认仅保留已提取的第一个经停点，或修改需求。"
    if request.arrival_deadline and request.departure_time and request.arrival_deadline <= request.departure_time:
        questions["arrival_deadline"] = "到达时限不晚于出发时间，请明确选择到达日期和时间。"
    return request, questions, {"parser": parser_used, "warnings": warnings, "parser_usage":usage}
