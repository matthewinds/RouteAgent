"""Real online Streamlit UI: credentials stay in the backend, never in widgets."""
import html
import queue
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import folium
import streamlit as st
from streamlit_folium import st_folium
from route_agent import plan_route
from route_agent.config import Settings,ROOT
from route_agent.models import PlanningResult
from route_agent.poi_categories import category_label
from route_agent.traffic import future_departure, past_departure
from route_agent.ranking import (explain,time_notes,scheduled_traffic,historical_traffic,verification_summary,
    base_arrival_note,estimated_arrival_note,CHECK_LABELS,place_label,poi_search_summary,itinerary_label,exposure_reference)

SG = ZoneInfo("Asia/Singapore")
MODES = {"driving":"自驾","walking":"全程步行","drive_walk":"自驾＋步行接驳","transit":"公交／地铁","bus":"公交","rail":"轨道交通"}
LEG_MODES = {"driving":"自驾","walking":"步行","bus":"公交","rail":"地铁"}
STATES = {"verified":"基础检查通过","violated":"不满足条件","unverified":"待核实"}
LABELS = CHECK_LABELS
# Mode colours follow the strip map; rail legs take their real MRT/LRT line colour.
MODE_COLORS = {"driving":"#00804A","walking":"#8C99A1","bus":"#2F6DB5","rail":"#4B5A63"}
LINE_COLORS = {"NS":"#D42E12","EW":"#009645","CG":"#009645","NE":"#9900AA","CC":"#FA9E0D","CE":"#FA9E0D",
    "DT":"#005EC4","TE":"#9D5B25","BP":"#748477","SK":"#748477","SW":"#748477","SE":"#748477","PG":"#748477",
    "PE":"#748477","PW":"#748477"}
st.set_page_config(page_title="RouteAgent · 新加坡出行规划",page_icon="🧭",layout="wide")
if st.session_state.get("online_ui_version")!=5:
    for old_key in ("job","result","events_queue","events","pending_text","pending_clarifications",
                    "last_example","example-choice"):
        st.session_state.pop(old_key,None)
    st.session_state.online_ui_version = 5
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@500;600&display=swap');
:root{--ra-ink:#17232B;--ra-slate:#5D6A72;--ra-rule:#D5DCD8;--ra-mist:#EEF1EF;--ra-paper:#FFFFFF;--ra-signal:#00804A;--ra-stop:#D99A00}
.block-container{max-width:1120px;padding-top:2.75rem;padding-bottom:4rem}
.ra-hero{margin:0 0 1.25rem}
.ra-hero h1{font-size:2.5rem;line-height:1.15;font-weight:700;letter-spacing:-.02em;color:var(--ra-ink);padding:0;margin:0 0 .5rem}
.ra-hero p{color:var(--ra-slate);font-size:1rem;line-height:1.7;max-width:42em;margin:0}
[data-testid="stForm"]{background:var(--ra-paper);border:1px solid var(--ra-rule);border-radius:16px;padding:1.25rem 1.25rem 1rem}
[data-testid="stForm"] textarea{background:var(--ra-paper);font-size:1.05rem;line-height:1.65}
[data-testid="stMetric"]{background:var(--ra-paper);border:1px solid var(--ra-rule);border-radius:10px;padding:.7rem .9rem}
[data-testid="stMetricLabel"] p{font-size:.8rem;color:var(--ra-slate)}
[data-testid="stMetricValue"]{font-family:Sora,"Noto Sans SC",sans-serif;font-size:1.2rem;font-weight:600}
[data-testid="stTabs"] [data-baseweb="tab-list"]{gap:1.5rem}
[data-testid="stTabs"] button p{font-size:.95rem}
[data-testid="stTabs"]{margin-top:1.5rem}
[data-testid="stTabs"] h3{font-size:1.05rem;font-weight:700;padding:1rem 0 .25rem}
.ra-num{font-family:Sora,"Noto Sans SC",sans-serif;font-feature-settings:"tnum"}
.ra-trip{background:var(--ra-paper);border:1px solid var(--ra-rule);border-radius:16px;padding:1.5rem 1.6rem 1.4rem;margin:.25rem 0 1rem}
.ra-trip-top{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:1rem 2rem}
.ra-route{font-size:1.15rem;font-weight:700;color:var(--ra-ink);line-height:1.4;margin:0}
.ra-route span{color:var(--ra-slate);font-weight:400;margin:0 .4rem}
.ra-time{text-align:right;line-height:1}
.ra-time b{font-family:Sora,sans-serif;font-size:3rem;font-weight:600;letter-spacing:-.03em;color:var(--ra-ink)}
.ra-time small{font-size:1rem;color:var(--ra-slate);margin-left:.3rem}
.ra-time div{font-size:.85rem;color:var(--ra-slate);margin-top:.45rem}
.ra-badge{display:inline-block;font-size:.8rem;padding:.15rem .6rem;border-radius:999px;border:1px solid currentColor;margin-top:.6rem}
.ra-badge.verified{color:var(--ra-signal)}.ra-badge.unverified{color:#8A6A00}.ra-badge.violated{color:#B3261E}
.ra-strip{display:flex;align-items:flex-start;margin:1.6rem 0 .2rem;overflow-x:auto;padding-bottom:.3rem}
.ra-stop{flex:0 0 auto;width:6.5rem;display:flex;flex-direction:column;align-items:center;text-align:center;padding-top:1.7rem}
.ra-stop:first-child,.ra-stop:last-child{width:7.5rem}
.ra-dot{width:16px;height:16px;border-radius:50%;background:var(--ra-paper);border:4px solid var(--ra-ink);box-sizing:border-box}
.ra-stop.poi .ra-dot{border-color:var(--ra-stop);width:20px;height:20px;margin-top:-2px}
.ra-stop-name{font-size:.8rem;line-height:1.35;color:var(--ra-ink);margin-top:.5rem;word-break:break-word}
.ra-stop-tag{font-size:.72rem;color:#8A6A00;margin-top:.15rem}
.ra-seg{flex:1 1 4rem;min-width:4.5rem;margin:0 -2.6rem;display:flex;flex-direction:column;align-items:center;position:relative;z-index:0}
.ra-stop{position:relative;z-index:1}
.ra-bar{height:8px;width:100%;border-radius:4px;background:var(--c);margin-top:4px}
.ra-seg.walking .ra-bar{background:repeating-linear-gradient(90deg,var(--c) 0 7px,transparent 7px 12px);height:4px;margin-top:6px}
.ra-seg-label{font-size:.78rem;line-height:1.7rem;height:1.7rem;color:var(--ra-slate);white-space:nowrap;padding:0 2.7rem}
.ra-seg-label b{color:var(--c);font-weight:700}
.ra-progress{border-left:3px solid var(--ra-rule);margin:.5rem 0 0 .4rem;padding-left:1.1rem}
.ra-progress p{position:relative;margin:0 0 .65rem;color:var(--ra-slate);font-size:.92rem}
.ra-progress p:before{content:"";position:absolute;left:-1.62rem;top:.38rem;width:10px;height:10px;border-radius:50%;background:var(--ra-paper);border:2px solid var(--ra-rule)}
.ra-progress p:last-child{color:var(--ra-ink);font-weight:500}
.ra-progress p:last-child:before{border-color:var(--ra-signal);background:var(--ra-signal)}
.ra-brand{font-family:Sora,sans-serif;font-size:1.25rem;font-weight:600;color:var(--ra-ink);margin:0}
.ra-services{list-style:none;padding:0!important;margin:.5rem 0!important}
.ra-services li{margin:0!important}
.ra-services li{display:flex;justify-content:space-between;gap:.75rem;font-size:.88rem;padding:.42rem 0;border-bottom:1px solid var(--ra-rule)}
.ra-services li span:last-child{color:var(--ra-slate)}
.ra-services li i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:.55rem;background:#B8C1BD}
.ra-services li.ok i{background:var(--ra-signal)}
@media (max-width:640px){
 .ra-hero h1{font-size:1.9rem}
 .ra-time{text-align:left}
 .ra-strip{flex-direction:column;align-items:stretch;margin-top:1.2rem}
 .ra-stop,.ra-stop:first-child,.ra-stop:last-child{width:auto;flex-direction:row;align-items:center;text-align:left;gap:.75rem;padding-top:0}
 .ra-stop-name{margin-top:0}
 .ra-seg{margin:-2px 0 -2px 6px;padding:0;flex-direction:row;align-items:center;min-height:2.6rem}
 .ra-bar,.ra-seg.walking .ra-bar{width:4px;height:auto;align-self:stretch;margin:0;border-radius:2px}
 .ra-seg.walking .ra-bar{background:repeating-linear-gradient(180deg,var(--c) 0 6px,transparent 6px 10px)}
 .ra-seg-label{order:1;margin:0 0 0 1.1rem;padding:0;height:auto}
 [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]){flex-direction:row;flex-wrap:wrap;gap:.5rem}
 [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])>div{min-width:calc(50% - .25rem);flex:1 1 calc(50% - .25rem)}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>""",unsafe_allow_html=True)
@st.cache_resource
def workers():
    return ThreadPoolExecutor(max_workers=2,thread_name_prefix="online-planner")

@st.cache_resource
def link_index_maintenance():
    # Geometry-only LTA link index; trips fall back to sequential paging until it exists.
    from route_agent.lta_index import ensure_index_async
    return ensure_index_async(Settings())
link_index_maintenance()

def submit(text,clarification):
    current = st.session_state.get("job")
    if current is not None and not current.done():
        st.info("当前任务仍在运行，请等待结果。")
        return
    settings = Settings()
    events = queue.Queue()
    st.session_state.job = workers().submit(plan_route,text,clarification,settings=settings,progress=events.put)
    st.session_state.events_queue = events
    st.session_state.events = []
    st.session_state.pending_text = text
    st.session_state.pending_clarifications = clarification
    st.session_state.result = None

def minutes(value):
    return f"{value/60:.1f} 分钟" if value is not None else "尚未核实"

def leg_color(leg):
    if leg.mode=="rail" and leg.service_name:
        code=re.search(r"\b(NS|EW|CG|NE|CC|CE|DT|TE|BP|SK|SW|SE|PG|PE|PW)",leg.service_name.upper())
        if code:
            return LINE_COLORS[code.group(1)]
    return MODE_COLORS.get(leg.mode,"#4B5A63")

def stops_label(route):
    """All stops in visiting order; single-stop routes keep their one POI."""
    if route.stops:
        return " → ".join(place_label(p) for p in route.stops)
    return place_label(route.poi) if route.poi else "无"

def boundary_places(route):
    """(place, tag) at the start and after each leg. Provider endpoints can be
    placeholders such as "Destination", so stops come from the route's indices."""
    after={index:(place,"经停 "+str(n)) for n,(index,place) in enumerate(zip(route.stop_leg_indices,route.stops),1)}
    if route.poi is not None and route.poi_leg_index is not None:
        after[route.poi_leg_index]=(route.poi,"经停")
    nodes=[(route.origin,"")]
    for index,leg in enumerate(route.legs):
        if index in after:
            nodes.append(after[index])
        elif index==len(route.legs)-1:
            nodes.append((route.destination,""))
        elif route.poi is not None and route.poi_leg_index is None and leg.destination.id==route.poi.id:
            nodes.append((route.poi,"经停"))
        elif route.parking is not None and leg.destination.id==route.parking.id:
            nodes.append((route.parking,"停车"))
        else:
            nodes.append((leg.destination,""))
    return nodes

def trip_card(route,request,index):
    """The recommended trip as a line-map strip: stops are stations, legs are coloured lines."""
    best = route.total_s if route.total_s is not None else route.provider_total_s
    basis = "预计总用时" if route.total_s is not None else "基础估计，未含路况" if best is not None else "总用时尚未核实"
    arrival = ""
    if best is not None and request.departure_time:
        arrival = "约 "+(request.departure_time+timedelta(seconds=best)).astimezone(SG).strftime("%H:%M")+" 到达，"
    time_html = (f'<b>{best/60:.0f}</b><small>分钟</small>' if best is not None else '<b>—</b>')
    e = html.escape
    parts = []
    def stop(place,tag=""):
        is_poi = tag.startswith("经停")
        parts.append(f'<div class="ra-stop{" poi" if is_poi else ""}"><div class="ra-dot"></div>'
            f'<div class="ra-stop-name">{e(place.name)}</div>'+(f'<div class="ra-stop-tag">{tag}</div>' if tag else "")+'</div>')
    nodes = boundary_places(route)
    stop(*nodes[0])
    for leg,node in zip(route.legs,nodes[1:]):
        # Rail legs already carry their line colour, so the code alone names them.
        name = leg.service_name if leg.mode=="rail" and leg.service_name else LEG_MODES.get(leg.mode,leg.mode)+(" "+leg.service_name if leg.service_name else "")
        parts.append(f'<div class="ra-seg {leg.mode}" style="--c:{leg_color(leg)}">'
            f'<div class="ra-seg-label"><b>{e(name)}</b> {leg.provider_duration_s/60:.0f} 分</div><div class="ra-bar"></div></div>')
        stop(*node)
    fare = f"S${route.fare_sgd:.2f}" if route.fare_sgd is not None else "费用未知"
    st.markdown(f'''<div class="ra-trip"><div class="ra-trip-top"><div>
<p class="ra-route">{e(route.origin.name)}<span>到</span>{e(route.destination.name)}</p>
<span class="ra-badge {route.feasibility}">{STATES[route.feasibility]}</span></div>
<div class="ra-time">{time_html}<div>{arrival}{basis}</div><div class="ra-num">{fare}，{route.distance_m/1000:.1f} 公里</div></div></div>
<div class="ra-strip">{"".join(parts)}</div></div>''',unsafe_allow_html=True)

def map_view(routes, selected):
    route = next(r for r in routes if r.id==selected)
    view = folium.Map(location=[route.origin.lat,route.origin.lon],zoom_start=14,tiles="OpenStreetMap",control_scale=True)
    bounds = []
    for i,r in enumerate(routes):
        for leg in r.legs:
            coords = [[lat,lon] for lon,lat,*_ in leg.geometry["coordinates"]]
            bounds.extend(coords)
            chosen = r.id==selected
            folium.PolyLine(coords,color=leg_color(leg) if chosen else "#8C99A1",
                weight=6 if chosen else 3,opacity=1 if chosen else .35,dash_array="8 6" if leg.mode=="walking" else None,
                tooltip=f"方案 {i+1} · {MODES[leg.mode]}",popup=f"{leg.distance_m/1000:.2f} 公里 · {leg.source} 服务预计 {leg.provider_duration_s/60:.1f} 分钟").add_to(view)
    for place,label,color,icon in [(route.origin,"出发","green","flag"),(route.destination,"到达","darkblue","flag"),
            (route.poi,"经停","orange","coffee"),(route.parking,"停车转换","purple","car"),
            *[(stop_place,"经停 "+str(i+1),"orange","coffee") for i,stop_place in enumerate(route.stops)]]:
        if place:
            if label.startswith("经停") and place.category in ("fuel","amenity:fuel"):
                icon="tint"
            folium.Marker([place.lat,place.lon],tooltip=html.escape(label+" · "+place_label(place)),
                popup=folium.Popup(html.escape(place_label(place))),
                icon=folium.Icon(color=color,icon=icon,prefix="fa")).add_to(view)
    if bounds:
        view.fit_bounds(bounds)
    st_folium(view,height=420,use_container_width=True,returned_objects=[],key="map-"+selected)
    st.caption("实线为车行或公共交通，虚线为步行；没有已验证路线的分段之间不连线。地图数据 © OpenStreetMap contributors。")

def continuation_values(result, previous):
    values={key:value for key,value in result.request.model_dump(mode="json",
        exclude={"original_text","evidence"}).items() if value is not None}
    values.update(previous)
    source=result.context.get("departure_time_source") or previous.get("departure_time_source") or (
        "explicit" if result.request.evidence.get("departure_time") else "default_now")
    values["departure_time_source"]=source
    if source=="default_now":
        values.pop("departure_time",None)
    return values

def clarifications_form(result):
    if not result.questions:
        return
    st.subheader("还需要你确认")
    # Continue the already validated request; a fresh LLM extraction must not
    # drop constraints merely because the user is confirming a location.
    values=continuation_values(result,st.session_state.get("pending_clarifications",{}))
    searches = {**values.get("place_searches",{}),**result.context.get("place_searches",{})}
    if searches:
        values["place_searches"] = searches
    choices = result.context.get("choices",{})
    unresolved_choices = []
    with st.form("clarify"):
        for field,prompt in result.questions.items():
            if field.endswith("_place_id") and choices.get(field):
                options = choices[field]
                role = "poi" if field=="poi_place_id" else field.removesuffix("_place_id")
                search = searches.get(role,{})
                kinds={"name_alias":"名称／语言别名","spelling_candidate":"可能的拼写修正","description_candidate":"自然语言描述","source_address":"公开来源的地址线索"}
                if search.get("match_kind") in kinds:
                    st.caption("AI 按「"+search["query"]+"」提出"+kinds[search["match_kind"]]+"候选；位置来自真实查询，请选择实际要去的地点。")
                if search.get("search_queries"):
                    st.caption("根据「"+search["query"]+"」搜索到以下地图候选，请确认实际地点。")
                for note in dict.fromkeys(x.get("access_note") for x in options if x.get("access_note")):
                    st.info(note)
                for url in dict.fromkeys(x.get("source_url") for x in options if x.get("source_url")):
                    st.caption("地址来源：[查看原始记录]("+url+")")
                st.dataframe([{"地点":x["name"],"地址":x.get("address") or "见地图名称中的区域信息","来源":x.get("source") or "旧记录未保留来源名称"} for x in options],hide_index=True,use_container_width=True)
                values.pop(field,None)
                ident = st.selectbox(prompt,[x["id"] for x in options],index=None,placeholder="请选择你确认的地点",
                    format_func=lambda i,items=options:next(x["name"] for x in items if x["id"]==i),key="clarify-"+field+"-"+result.run_id)
                replacement = st.text_input("候选不正确？重新输入地点名称或经纬度",key="replace-"+field+"-"+result.run_id)
                if replacement.strip():
                    values[role if role!="poi" else "poi_name"] = replacement.strip()
                    searches.pop(role,None)
                elif ident:
                    values[field] = ident
                else:
                    unresolved_choices.append(field)
            elif field=="mode":
                choice=st.radio(prompt,["全程步行","公交／地铁"],index=None,key="clarify-mode-"+result.run_id)
                if choice is None:
                    unresolved_choices.append(field)
                else:
                    values[field]="walking" if choice=="全程步行" else "transit"
            elif field=="car_access":
                choice=st.radio(prompt,["有可用车辆，愿意自驾","没有车辆或不愿意自驾"],index=None,key="clarify-car-access-"+result.run_id)
                if choice is None:
                    unresolved_choices.append(field)
                else:
                    values[field]=choice=="有可用车辆，愿意自驾"
            elif field=="origin_access_confirmed":
                values[field]=st.checkbox(prompt,key="clarify-origin-access-"+result.run_id)
            elif field=="arrival_deadline" and choices.get(field):
                # The same clock words can mean different times; never preselect the model's reading.
                readings=choices[field]
                other="其他时间"
                choice=st.radio(prompt,[x["id"] for x in readings]+[other],index=None,key="clarify-deadline-"+result.run_id,
                    format_func=lambda i,items=readings:next((x["name"] for x in items if x["id"]==i),i))
                if choice==other:
                    default=result.request.arrival_deadline or datetime.now(SG)
                    values[field]=st.datetime_input("请选择到达时限",value=default.astimezone(SG).replace(tzinfo=None),
                        key="clarify-deadline-other-"+result.run_id).replace(tzinfo=SG).isoformat()
                elif choice is None:
                    unresolved_choices.append(field)
                else:
                    values[field]=choice
            elif field in ("departure_time","arrival_deadline"):
                default = getattr(result.request,field) or datetime.now(SG)
                values[field] = st.datetime_input(prompt,value=default.replace(tzinfo=None),key="clarify-"+field).replace(tzinfo=SG).isoformat()
                if field=="departure_time":
                    values["departure_time_source"]="explicit"
            elif field in ("stop_duration_s","parking_duration_s","max_duration_s"):
                value = st.number_input(prompt,min_value=0.0,value=None,step=1.0,key="clarify-"+field)
                if value is not None:
                    values[field] = value*60
            elif field=="max_walking_m":
                value = st.number_input(prompt,min_value=0.0,value=None,step=100.0,key="clarify-"+field)
                if value is not None:
                    values[field] = value
            elif field=="multiple_stops":
                values[field] = not st.checkbox(prompt,key="clarify-multiple-stops")
            elif field=="parking_access_confirmed":
                values[field] = st.checkbox(prompt,key="clarify-parking-access")
            elif field in ("require_dry","weather_required","prefer_avoid_rain","avoid_highways","require_open"):
                values[field] = st.checkbox(prompt,value=bool(getattr(result.request,field)),key="clarify-"+field)
            else:
                values[field] = st.text_input(prompt,value=getattr(result.request,field,None) or "",key="clarify-"+field)
        confirmed = st.form_submit_button("确认并继续在线规划",type="primary")
    if confirmed:
        if unresolved_choices:
            st.warning("请先完成所有选择（地点或到达时限）；不会默认使用第一项。")
            return
        submit(st.session_state.pending_text,values)
        st.rerun()

def understood_request(req):
    rows = {"起点":req.origin,"终点":req.destination,"方式":MODES.get(req.mode,"自动比较组合"),
        "出发时间":req.departure_time.strftime("%Y-%m-%d %H:%M（新加坡时间）") if req.departure_time else "未指定",
        "到达时限":req.arrival_deadline.strftime("%Y-%m-%d %H:%M（新加坡时间）") if req.arrival_deadline else "未指定",
        "希望经济实惠":req.prefer_low_cost,"希望尽快到达":req.prefer_fastest,"需要天气参考":req.weather_required,
        "已确认可自驾":req.car_access,"总时间上限（分钟）":req.max_duration_s/60 if req.max_duration_s else None,
        "经停":req.poi_name or ("由系统选择沿途"+category_label(req.poi_category) if req.poi_category else None),"必须经停":req.poi_required,"停留（分钟）":req.stop_duration_s/60 if req.stop_duration_s is not None else None,
        "避开高速":req.avoid_highways,"累计步行上限（米）":req.max_walking_m,
        "停车预留（分钟）":req.parking_duration_s/60 if req.parking_duration_s is not None else None,
        "避雨偏好":req.prefer_avoid_rain,"要求全程不淋雨":req.require_dry}
    # Only show what the request actually contains; empty fields are noise.
    shown = {k:("是" if v is True else "否" if v is False else v) for k,v in rows.items() if v not in (None,"未指定",False)}
    st.dataframe([{"项目":k,"内容":str(v)} for k,v in shown.items()],hide_index=True,use_container_width=True)

def render(result):
    # Rehydrate session results after hot reload when the model gains optional
    # fields, without losing the user's confirmations or resetting their run.
    result=PlanningResult.model_validate(result.model_dump())
    routes = [result.recommended,*result.alternatives] if result.recommended else result.unverified
    traffic = result.context.get("traffic",{})
    scheduled = scheduled_traffic(result.request,traffic)
    historical = historical_traffic(result.request,traffic)
    traffic_unavailable = scheduled or historical
    traffic_label = "当前路况不适用" if scheduled else "出发时段无有效路况"
    message = "已生成参考方案。"+verification_summary(routes[0]) if result.status=="unverified" and routes else result.message
    {"verified":st.success,"unverified":st.info,"needs_clarification":st.info,"search_exhausted":st.info,"tool_error":st.error}[result.status](message)
    for missing in result.context.get("unmet_preferences",[]):
        st.warning(missing)
    clarifications_form(result)

    route = None
    if routes:
        st.subheader("参考方案" if result.status=="unverified" else "推荐与备选" if result.recommended else "待核实方案")
        selected = st.radio("选择方案",[r.id for r in routes],horizontal=True,key="route-"+result.run_id,
            label_visibility="collapsed",
            format_func=lambda ident:next(f"方案 {i+1} · {MODES[r.mode]}" for i,r in enumerate(routes) if r.id==ident))
        index = next(i for i,r in enumerate(routes) if r.id==selected)
        route = routes[index]
        trip_card(route,result.request,index)
        cols = st.columns(5)
        for col,label,value,tip in zip(cols,["基础预计总时长","交通修正总时长","行驶与步行距离","累计步行","LTA 交通覆盖"],
            [minutes(route.provider_total_s),(("临近出发时更新" if scheduled else "历史路况未接入") if traffic_unavailable and route.total_s is None else minutes(route.total_s)) if route.mode in ("driving","drive_walk") else "不适用",
             f"{route.distance_m/1000:.2f} 公里",f"{route.walking_m:.0f} 米",
             traffic_label if traffic_unavailable and route.mode in ("driving","drive_walk") else f"{route.traffic_coverage:.0%}" if route.traffic_coverage is not None else "不适用" if route.mode in ("walking","transit") else "未查询路况"],
            ["地图／路线服务给出的时间，加上已确认的停留与停车预留。","用 LTA 实时车速修正后的时间，只适用于自驾。",
             None,None,"路线中有 LTA 实时车速观测的道路比例。"]):
            col.metric(label,value,help=tip)
        if route.total_s is None and route.estimated_total_s is not None:
            st.metric("部分路况预计总时长",minutes(route.estimated_total_s))
            st.caption(route.estimated_time_basis)
            st.caption(estimated_arrival_note(route,result.request))
        arrival_note = base_arrival_note(route,result.request)
        if arrival_note:
            st.caption(arrival_note)
        for note in time_notes(route,traffic,result.request):
            st.info(note)
        map_view(routes,selected)
    elif result.explanation:
        st.write(result.explanation)

    tabs = (["方案比较","行程细节","检查与天气"] if routes else [])+["交通数据","已理解的需求","运行记录"]
    panes = dict(zip(tabs,st.tabs(tabs)))
    if routes:
        with panes["方案比较"]:
            comparison_tab(result,routes,scheduled,traffic_unavailable,traffic_label)
        with panes["行程细节"]:
            details_tab(result,route,scheduled)
        with panes["检查与天气"]:
            checks_tab(result,route,scheduled)
    with panes["交通数据"]:
        traffic_tab(result,routes,traffic)
    with panes["已理解的需求"]:
        understood_request(result.request)
    with panes["运行记录"]:
        log_tab(result)
    st.download_button("下载本次规划记录",result.model_dump_json(indent=2),file_name=f"route-v2-{result.run_id}.json",mime="application/json")

def comparison_tab(result,routes,scheduled,traffic_unavailable,traffic_label):
    comparison=result.context.get("mode_comparison",{})
    if comparison:
        st.subheader("交通方式比较")
        st.caption("仅比较已查询到的服务估计。自驾费用不等于出租车或网约车报价；未取得的方式和费用无法参与完整比较。")
        st.dataframe([{"方式":MODES.get(o["mode"],o["mode"]),"参考预计时间":minutes(o["provider_duration_s"]),
            "费用参考（SGD）":f"S${o['fare_sgd']:.2f}" if o.get("fare_sgd") is not None else "未知",
            "费用说明":o.get("fare_basis","尚未核实")} for o in comparison.get("options",[])],hide_index=True,use_container_width=True)
        for failure in comparison.get("failures",[]):
            st.info(MODES.get(failure["mode"],failure["mode"])+"起终点直接查询未取得："+failure["message"])
    full_comparison=result.context.get("candidate_mode_comparison",{})
    if full_comparison:
        st.subheader("完整行程比较")
        st.caption("含真实经停绕行、步行及公共交通候车与换乘；自动探索的方式不会成为整程限制。")
        best={}
        for candidate in sorted(result.state.candidates,key=lambda r:({"verified":0,"unverified":1,"violated":2}[r.feasibility],r.score if r.score is not None else float("inf"),r.provider_total_s or float("inf"))):
            best.setdefault((candidate.mode,candidate.poi.id if candidate.poi else tuple(p.id for p in candidate.stops) or None),candidate)
        st.dataframe([{"分段方式":itinerary_label(r),"完整服务预计时间":minutes(r.provider_total_s),
            "经停":stops_label(r),"步行时间":minutes(r.walking_s),
            "候车与换乘等待":minutes(exposure_reference(r)["waiting_s"]),"状态":STATES[r.feasibility]}
            for r in list(best.values())[:12]],hide_index=True,use_container_width=True)
        for failure in full_comparison.get("failures",[]):
            st.info(MODES.get(failure["mode"],failure["mode"])+"完整行程未取得："+failure["message"])
        if full_comparison.get("partial_failures"):
            with st.expander("未参与比较的候选组合"):
                st.caption("以下组合未取得有效行程；已成功返回的其他组合保留并继续验证。")
                for failure in full_comparison["partial_failures"]:
                    st.write(MODES.get(failure["mode"],failure["mode"])+"："+failure["message"])
    st.subheader("所有方案")
    st.caption("状态只表示列出的检查结果，不代表最便宜、全局最快或天气有保证。")
    partial=any(r.estimated_total_s is not None and r.total_s is None for r in routes)
    st.dataframe([{"方案":f"方案 {i+1}","方式":itinerary_label(r),"基础预计总时长":minutes(r.provider_total_s),
        "交通修正总时长":(("临近出发时更新" if scheduled else "历史路况未接入") if traffic_unavailable and r.total_s is None else minutes(r.total_s)) if r.mode in ("driving","drive_walk") else "不适用","距离（公里）":round(r.distance_m/1000,2),
        **({"部分路况预计总时长":minutes(r.estimated_total_s)} if partial else {}),
        "步行（米）":round(r.walking_m),"经停":stops_label(r),
        "交通覆盖":traffic_label if traffic_unavailable and r.mode in ("driving","drive_walk") else f"{r.traffic_coverage:.0%}" if r.traffic_coverage is not None else "不适用" if r.mode in ("walking","transit") else "未查询路况",
        "费用参考（SGD）":f"S${r.fare_sgd:.2f}" if r.fare_sgd is not None else "未知",
        "状态":"待更新："+"、".join(LABELS.get(c.constraint,c.constraint) for c in r.checks if c.status=="unknown") if any(c.status=="unknown" for c in r.checks) and r.feasibility!="violated" else "基础检查通过；偏好比较不完整" if r.feasibility=="verified" and result.status=="unverified" else STATES[r.feasibility]} for i,r in enumerate(routes)],
        hide_index=True,use_container_width=True)
    if result.recommended and result.unverified:
        st.caption(f"另外有 {len(result.unverified)} 个待核实候选，不作为正式推荐。")

def details_tab(result,route,scheduled):
    st.write(explain(route,include_unknown=False))
    st.caption(route.time_basis if route.mode=="transit" else "基础总时长＝道路服务时间＋已确认的停留／停车预留；自驾未计入未知交通延误。")
    if route.mode=="transit":
        st.caption("LTA 的公交到站与地铁状态用于实时核对；当前未据此单独计算整程修正时间。候车或换乘变化时，需要重新查询完整行程。")
    st.subheader("分段行程")
    nodes=boundary_places(route)
    st.dataframe([{"段":i+1,"方式":MODES[x.mode],"起点":place_label(nodes[i][0]),"终点":place_label(nodes[i+1][0]),
        "线路":x.service_name or "—","距离（米）":round(x.distance_m),"服务预计时间":minutes(x.provider_duration_s),
        "LTA 时间估计":("临近出发时更新" if scheduled and x.traffic_duration_s is None else minutes(x.traffic_duration_s)) if x.mode=="driving" else "不适用"} for i,x in enumerate(route.legs)],
        hide_index=True,use_container_width=True)
    st.subheader("费用")
    st.write("费用参考："+(f"S${route.fare_sgd:.2f}" if route.fare_sgd is not None else "未知")+"。"+route.fare_basis)
    fare_subtotal=getattr(route,"fare_known_subtotal_sgd",None)
    fare_components=getattr(route,"fare_components",[])
    if route.fare_sgd is None and fare_subtotal is not None:
        st.caption(f"已核实部分的费用小计 S${fare_subtotal:.2f}；尚有未核实费用，不能当作整程总价。")
    if fare_components:
        with st.expander("交通费用明细与计费依据"):
            st.dataframe([{"线路":part.get("service") or "MRT/LRT","上车":part.get("boarding"),
                "下车":part.get("alighting"),"本段应计金额（SGD）":f"S${part['fare_sgd']:.2f}" if part.get("fare_sgd") is not None else "未知",
                "计费行程":part.get("journey_group"),"换乘依据":part.get("transfer_basis"),
                "来源":part.get("source_url")} for part in fare_components],hide_index=True,use_container_width=True)
            if getattr(route,"fare_profile",None):
                st.caption(route.fare_profile)
    if result.request.prefer_avoid_rain:
        exposure=exposure_reference(route)
        st.caption("步行 "+minutes(exposure["walking_s"])+"；候车与换乘等待 "+minutes(exposure["waiting_s"])+"。"+exposure["basis"])
    if route.poi:
        st.subheader("经停地点")
        st.write(place_label(route.poi))
        if result.request.poi_category and not result.request.poi_name:
            st.caption("由系统搜索沿途"+category_label(result.request.poi_category)+"，比较真实路线后选择，无需指定地点地址。")
        if route.poi.source_url:
            st.link_button("查看经停地点地图来源",route.poi.source_url)
    if route.stops:
        st.subheader("经停地点（按顺序）")
        for number,(stop_place,dwell) in enumerate(zip(route.stops,route.stop_durations_s),1):
            st.write(f"{number}. {place_label(stop_place)} · 停留 {dwell/60:g} 分钟")
        st.caption("各经停点由系统沿途搜索真实地点并比较完整行程后选择；停留时间计入总时长。")
    if route.parking:
        st.subheader("停车转换点")
        p = route.parking
        st.write(p.name)
        st.metric("查询时可用车位",str(p.available_lots) if p.available_lots is not None else "未知")
        st.caption("LTA 当前车位不是到达时的预约或保证；停车出入口需要用户核实。")
    with st.expander("DeepSeek 推荐依据与比较指标"):
        st.write("模型只能选择通过后端检查的候选；下面是工具计算的比较权重。")
        st.json({"权重":result.context.get("ranking_weights"),"引用证据":result.context.get("model_evidence_ids")})

def checks_tab(result,route,scheduled):
    st.subheader("逐项检查")
    st.dataframe([{"条件":LABELS.get(c.constraint,c.constraint),"类型":"必须满足" if c.hard else "需求与偏好",
        "状态":{"pass":"通过","fail":"违反","unknown":"待更新证据"}[c.status],
        "说明":"当前路况不能核实预约出发时段的到达时间；目前只有基础行程估计。" if scheduled and c.status=="unknown" and c.constraint in ("arrival_deadline","duration") and route.mode in ("driving","drive_walk") else c.reason} for c in route.checks],hide_index=True,use_container_width=True)
    if result.context.get("weather",{}).get("missing"):
        st.info("天气工具："+result.context["weather"]["missing"])
    if route.weather:
        st.subheader("天气预报参考")
        weather = route.weather
        if weather.status=="available":
            window=" — ".join(t.astimezone(SG).strftime("%m-%d %H:%M") for t in (weather.valid_start,weather.valid_end) if t)
            st.write(f"来源：{weather.source}，参考时段 {window}（新加坡时间）")
            rows=[dict(sample) for sample in weather.samples or weather.areas]
            for sample in rows:
                for key in ("降水时段开始","降水时段结束"):
                    if sample.get(key):
                        sample[key]=datetime.fromisoformat(sample[key]).astimezone(SG).strftime("%m-%d %H:%M")
            st.dataframe(rows,hide_index=True,use_container_width=True)
        else:
            st.info(weather.unknown_reason or "缺少覆盖采样点和行程时段的有效预报，天气保持未知。")
        st.caption(weather.timing_basis)
        st.caption(weather.mapping_basis+" 不能证明全程实际无雨，也不提供有遮蔽通道保证。")
        st.caption(weather.risk_basis)
        if weather.source=="Open-Meteo":
            st.caption("天气数据来源：[Open-Meteo](https://open-meteo.com/)。道路采样不表示预报具备街道级空间分辨率。")

def traffic_tab(result,routes,traffic):
    selection = result.context.get("mode_selection",{})
    if selection:
        st.info(("探索方式：" if selection.get("provisional") else "出行方式：")+MODES.get(selection["selected"],selection["selected"])+" · "+selection["basis"])
        if selection.get("car_access_assumption"):
            st.caption("此驾车方案以你可以使用车辆为前提；若不方便驾车，请在需求中说明。")
    st.subheader("LTA 交通信息")
    st.caption("LTA 提供道路、公交到站、地铁中断、车站拥挤、设施维护及其他交通数据；公交／地铁组合行程由 OneMap 计算，驾车／步行由 OSRM 计算。Agent 按需求查询，实时信息与历史统计分开使用。")
    traffic_called=any(t.tool=="get_traffic" for t in result.state.tools)
    lta_queries=result.context.get("lta_queries",[])
    if traffic.get("missing"):
        st.info(("道路实时路况未采集：" if traffic.get("applicability") in ("future_departure","past_departure") else "道路路况查询未完成：")+traffic["missing"])
    elif traffic.get("retrieved_at"):
        st.info("已取得 LTA 道路路况，采集时间："+traffic["retrieved_at"])
        st.caption(f"车速记录 {traffic.get('speed_band_count','未记录')} 条，事故记录 {traffic.get('incident_count','未记录')} 条。是否匹配本次路线及覆盖比例见检查结果；采集成功不代表所有道路都有观测。")
    elif not traffic_called:
        if result.status=="needs_clarification":
            st.info("尚未查询 LTA：规划仍在确认需求或地点，还未进入道路路况检查。")
        elif result.state.candidates and all(r.mode in ("walking","transit") for r in result.state.candidates):
            st.info("本次未调用 LTA 道路车速：当前候选为步行或公交／地铁；公共交通时刻来自 OneMap。公交到站和地铁状态的 LTA 核对结果见下方。" if lta_queries else "本次未调用 LTA 道路车速：当前候选为步行或公交／地铁；公共交通时刻来自 OneMap。")
        else:
            st.info("本次尚未查询 LTA 道路路况；服务配置状态不等于本次查询成功。")
        if future_departure(result.request.departure_time):
            st.caption("预约出发行程：当前 LTA 路况不能预测该出发时段，临近出发时重新规划可查询当时路况。")
        elif past_departure(result.request.departure_time):
            st.caption("出发时间已早于实时路况有效窗口，当前 LTA 数据不能还原当时交通。")
    if lta_queries:
        from route_agent.lta_data import DATASETS
        st.dataframe([{"查询信息":DATASETS.get(q["dataset"],{}).get("label",q["dataset"]),
            "结果":"已取得" if q.get("status")=="available" else "未取得",
            "采集时间":q.get("retrieved_at","—"),"数据范围":q.get("temporal_basis","—"),
            "完整性":"文件尚未读取" if q.get("file") and not q["file"].get("read") else "部分文件内容" if (q.get("file") or {}).get("truncated") else
                "已读完本次查询" if q.get("collection",{}).get("complete") else "部分分页" if q.get("status")=="available" else q.get("message","服务未提供可用数据")}
            for q in lta_queries],hide_index=True,use_container_width=True)
        with st.expander("LTA 与路线的核对结果"):
            for route_id,entry in result.context.get("lta_route_checks",{}).items():
                candidate=next((r for r in result.state.candidates if r.id==route_id),None)
                if candidate and candidate in routes:
                    for c in entry["checks"]:st.write(LABELS.get(c["constraint"],c["constraint"])+"："+c["reason"])
                    for advisory in entry.get("advisories",[]):
                        st.write(DATASETS.get(advisory["dataset"],{}).get("label",advisory["dataset"]));st.json(advisory)
    if result.context.get("lta_images"):
        with st.expander("LTA 道路摄像头参考"):
            from pathlib import Path
            for image in result.context["lta_images"]:
                path=Path(image["path"])
                if path.is_file() and path.resolve().is_relative_to((ROOT/"outputs"/"lta-media").resolve()):
                    st.image(str(path),caption="摄像头 "+image["camera_id"]+" · "+image["retrieved_at"])
    searches=result.context.get("poi_searches",[])
    if searches:
        st.subheader("经停地点搜索")
        st.caption(poi_search_summary(searches))
        for search in searches:
            st.caption({"route":"参考路线沿途","origin":"起点附近","destination":"终点附近"}.get(search["near"],search["near"])
                +f"，半径 {search['radius_m']} 米，返回 {search['count']} 个候选。"+search["basis"])

def log_tab(result):
    if result.context.get("nea_weather_supplement"):
        st.write("NEA 官方区域预报补充：独立于 Open-Meteo 网格预报，不证明逐路段无雨。")
        st.json(result.context["nea_weather_supplement"])
    roads = result.context.get("osm_roads",{})
    if roads:
        st.write("已查询的 OSM 道路属性：缺失标签保持未知，不代表实时通行状态。")
        st.dataframe([{"OSM ID":r["id"],"道路名":r["name"],**r["tags"]} for r in roads.values()],hide_index=True)
    st.caption(f"重规划 {result.metrics.get('replans',0)} 次，模型调用 {result.metrics.get('model_calls',0)} 次，外部请求 {result.metrics.get('http_calls',0)} 次")
    if result.state.stages:
        progress_track([e["message"] for e in result.state.stages],[e.get("elapsed_s") for e in result.state.stages])
    if result.state.tools:
        st.dataframe([{"工具":t.tool,"状态":t.status,"说明":t.summary,"调用 ID":t.call_id,"复用":t.cached,
            "耗时（毫秒）":round(t.duration_ms),"证据":", ".join(t.evidence_ids)} for t in result.state.tools],
            hide_index=True,use_container_width=True)
    with st.expander("原始证据记录"):
        st.json([e.model_dump(mode="json") for e in result.evidence])

def progress_track(messages,elapsed=None):
    elapsed = elapsed or [None]*len(messages)
    st.markdown('<div class="ra-progress">'+"".join(
        f'<p>{html.escape(m)}'+(f' <span class="ra-num">{t:.1f}s</span>' if t is not None else "")+'</p>'
        for m,t in zip(messages,elapsed))+'</div>',unsafe_allow_html=True)

with st.sidebar:
    st.markdown('<p class="ra-brand">RouteAgent</p>',unsafe_allow_html=True)
    st.caption("新加坡出行规划")
    settings = Settings()
    ready = settings.readiness()
    services = [("DeepSeek 理解需求",ready["DEEPSEEK_API_KEY"],"缺少 Key"),("ORS 地址定位",ready["ORS_API_KEY"],"缺少 Key"),
        ("LTA 交通数据",ready["LTA_API_KEY"],"缺少 Key"),("OneMap 公交与票价",ready.get("ONEMAP_TOKEN"),"未配置"),
        ("Tavily 网页地点检索",ready.get("TAVILY_API_KEY"),"未配置")]
    st.markdown('<ul class="ra-services">'+"".join(f'<li class="{"ok" if ok else ""}"><span><i></i>{name}</span><span>{"已配置" if ok else off}</span></li>'
        for name,ok,off in services)+'</ul>',unsafe_allow_html=True)
    st.caption("“已配置”只表示填写了 Key，不代表已验证服务可用。")
    if st.button("刷新配置状态",use_container_width=True):
        from dotenv import load_dotenv
        load_dotenv(ROOT/".env",override=True)
        st.rerun()
    routing = settings.routing_configuration()
    with st.expander("数据来源与限制"):
        st.caption("地点搜索使用地图、公司登记和公开网页来源；景点、酒店等也可检索，存在歧义时会请你确认实际地点。")
        if not ready.get("TAVILY_API_KEY"):
            st.caption("未配置 Tavily 时，地图未收录的办公室可能无法匹配；在本地 .env 配置 TAVILY_API_KEY 后点击“刷新配置状态”。")
        st.caption("OSRM 算路无需 Key，驾车"+("已填写" if routing["driving_configured"] else "缺少")+"端点，步行"+("已填写" if routing["walking_configured"] else "缺少")+"端点。公共 OSRM 仅供轻量试用，每秒最多一次；批量实验请配置自建服务。")
        if routing["public_driving_service"]:
            st.caption("当前公共服务不支持强制避高速；此类请求会提示更换算路服务。")
        st.caption("未接入公共交通或缺少票价时，不能证明最经济实惠或最快。")
        st.caption("OSM 道路与经停地点通过公开 Overpass 接口查询；Open-Meteo 天气为非商业公开接口；NEA 可作为官方区域补充。")
        st.caption("模型和服务调用可能产生费用或消耗配额。")
        if not ready["LTA_API_KEY"]:
            st.caption("全程步行无需 LTA；驾车实时路况与停车查询需要 LTA Key。")
    st.caption("模型："+settings.model)

st.markdown('<div class="ra-hero"><h1>今天要去哪？</h1><p>用一句话说清起点、终点和时间。顺路想去的地方、到达时限、预算都可以写上，交通方式交给系统比较。</p></div>',unsafe_allow_html=True)
job = st.session_state.get("job")
busy = job is not None and not job.done()
missing = settings.missing_required()
if missing:
    st.warning("请先在项目根目录 .env 填写："+ "、".join(missing)+"。填写后点击左侧“刷新配置状态”。不会使用离线规则或演示结果替代。")
with st.form("request-form"):
    text = st.text_area("你的出行需求",key="request_text",height=120,label_visibility="collapsed",
        placeholder="例如：明早 9 点从 Toa Payoh 出发去 NUS，路上买杯咖啡，10 点前到。")
    left,right = st.columns([3,1],vertical_alignment="center")
    left.caption("没写出发时间时，按现在出发规划。")
    start = right.form_submit_button("开始在线规划",type="primary",disabled=busy or bool(missing),use_container_width=True)
if start:
    if text.strip():
        same_request=text.strip()==st.session_state.get("pending_text","").strip()
        confirmed=dict(st.session_state.get("pending_clarifications",{})) if same_request else {}
        previous=st.session_state.get("result")
        if same_request and previous is not None:
            temporal=continuation_values(previous,confirmed)
            confirmed["departure_time_source"]=temporal["departure_time_source"]
            if temporal["departure_time_source"]=="default_now":
                confirmed.pop("departure_time",None)
        submit(text,dict(confirmed))
    else:
        st.warning("请先输入你的出行需求。")

@st.fragment(run_every="1s")
def monitor():
    job = st.session_state.get("job")
    if job is None:
        st.caption("可比较自驾、步行、公交与地铁组合，并加入一个顺路经停点。路线、时间和票价都来自真实地图与交通服务。")
        return
    events = st.session_state.events_queue
    while True:
        try:
            st.session_state.events.append(events.get_nowait())
        except queue.Empty:
            break
    if not job.done():
        messages = [e["message"] for e in st.session_state.events[-5:]] or ["已提交，正在理解你的需求"]
        progress_track(messages)
        st.caption("显示的是正在进行的步骤，不代表剩余时间。")
        return
    if st.session_state.get("result") is None:
        try:
            st.session_state.result = job.result()
        except Exception:
            st.error("规划没有完成。请检查左侧服务状态后重新提交。")
            return
        st.rerun()

if st.session_state.get("result") is not None:
    render(st.session_state.result)
else:
    monitor()
