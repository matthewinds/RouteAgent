"""Real online Streamlit UI: credentials stay in the backend, never in widgets."""
import html
import queue
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
import folium
import streamlit as st
from streamlit_folium import st_folium
from route_agent import plan_route
from route_agent.config import Settings,ROOT
from route_agent.ranking import explain,time_notes

SG = ZoneInfo("Asia/Singapore")
MODES = {"driving":"驾车","walking":"全程步行","drive_walk":"驾车＋步行接驳"}
LABELS = {"mode":"交通方式","required_poi":"必经地点","avoid_highways":"避开高速","duration":"总时间上限",
    "arrival_deadline":"到达时限","congestion":"拥堵上限","opening_hours":"营业时间","continuity":"分段连接",
    "walking_distance":"累计步行距离","parking":"当前停车位","parking_access":"停车转换入口",
    "named_parking":"指定停车点","dry_weather":"全程不淋雨","multiple_stops":"多个经停点"}
st.set_page_config(page_title="RouteAgent · DeepSeek 在线规划",page_icon="🧭",layout="wide")
if st.session_state.get("online_ui_version")!=3:
    for old_key in ("job","result","events_queue","events","pending_text","pending_clarifications",
                    "request_text","last_example","example-choice"):
        st.session_state.pop(old_key,None)
    st.session_state.online_ui_version = 3
st.markdown("""<style>.stApp{background:#f7f9f8}.block-container{max-width:1280px;padding-top:2rem}
h1,h2,h3{color:#153d35}[data-testid="stSidebar"]{background:#edf3f0}
[data-testid="stMetric"]{background:white;border:1px solid #dce7e0;padding:1rem;border-radius:14px}
[data-testid="stMetricValue"]{font-size:1.55rem}.hero{background:#143f35;color:white;padding:28px 32px;border-radius:20px;margin-bottom:24px}
.hero h1{color:white;font-size:30px}.hero p{color:#cee1d9}</style>""",unsafe_allow_html=True)

@st.cache_resource
def workers():
    return ThreadPoolExecutor(max_workers=2,thread_name_prefix="online-planner")

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

def map_view(routes, selected):
    route = next(r for r in routes if r.id==selected)
    view = folium.Map(location=[route.origin.lat,route.origin.lon],zoom_start=14,tiles="OpenStreetMap",control_scale=True)
    bounds = []
    for i,r in enumerate(routes):
        for leg in r.legs:
            coords = [[lat,lon] for lon,lat,*_ in leg.geometry["coordinates"]]
            bounds.extend(coords)
            chosen = r.id==selected
            folium.PolyLine(coords,color=("#158368" if leg.mode=="driving" else "#607eaa"),
                weight=6 if chosen else 3,opacity=1 if chosen else .3,dash_array="8 6" if leg.mode=="walking" else None,
                tooltip=f"方案 {i+1} · {MODES[leg.mode]}",popup=f"{leg.distance_m/1000:.2f} 公里 · OSRM {leg.provider_duration_s/60:.1f} 分钟").add_to(view)
    for place,label,color,icon in [(route.origin,"出发","green","flag"),(route.destination,"到达","blue","flag"),
            (route.poi,"经停","orange","coffee"),(route.parking,"停车转换","purple","car")]:
        if place:
            folium.Marker([place.lat,place.lon],tooltip=html.escape(label+" · "+place.name),
                icon=folium.Icon(color=color,icon=icon,prefix="fa")).add_to(view)
    if bounds:
        view.fit_bounds(bounds)
    st_folium(view,height=440,use_container_width=True,returned_objects=[],key="map-"+selected)
    st.caption("实线：驾车；虚线：步行。未验证的分段间隙不绘制虚构连接。地图数据 © OpenStreetMap contributors。")

def clarifications_form(result):
    if not result.questions:
        return
    st.subheader("补充或确认信息")
    # Continue the already validated request; a fresh LLM extraction must not
    # drop constraints merely because the user is confirming a location.
    values = {key:value for key,value in result.request.model_dump(mode="json",
        exclude={"original_text","evidence"}).items() if value is not None}
    values.update(st.session_state.get("pending_clarifications",{}))
    choices = result.context.get("choices",{})
    unresolved_choices = []
    with st.form("clarify"):
        for field,prompt in result.questions.items():
            if field in choices and choices[field]:
                options = choices[field]
                values.pop(field,None)
                ident = st.selectbox(prompt,[x["id"] for x in options],index=None,placeholder="请选择你确认的地点",
                    format_func=lambda i,items=options:next(x["name"] for x in items if x["id"]==i),key="clarify-"+field+"-"+result.run_id)
                replacement = st.text_input("候选不正确？重新输入地点名称或经纬度",key="replace-"+field+"-"+result.run_id)
                if replacement.strip():
                    values[field.removesuffix("_place_id") if field!="poi_place_id" else "poi_name"] = replacement.strip()
                elif ident:
                    values[field] = ident
                else:
                    unresolved_choices.append(field)
            elif field=="mode":
                values["travel_preferences"] = st.text_input("请描述你的出行偏好或限制",key="clarify-mode-preferences")
            elif field in ("departure_time","arrival_deadline"):
                default = getattr(result.request,field) or datetime.now(SG)
                values[field] = st.datetime_input(prompt,value=default.replace(tzinfo=None),key="clarify-"+field).replace(tzinfo=SG).isoformat()
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
            st.warning("请先选择确认的地点，或重新输入地点；不会默认使用第一项。")
            return
        submit(st.session_state.pending_text,values)
        st.rerun()

def render(result):
    {"verified":st.success,"unverified":st.warning,"needs_clarification":st.info,"search_exhausted":st.info,"tool_error":st.error}[result.status](result.message)
    if result.context.get("traffic",{}).get("missing"):
        st.info("交通工具："+result.context["traffic"]["missing"])
    if result.context.get("weather",{}).get("missing"):
        st.info("天气工具："+result.context["weather"]["missing"])
    selection = result.context.get("mode_selection",{})
    if selection:
        st.info("出行方式："+MODES.get(selection["selected"],selection["selected"])+" · "+selection["basis"])
        if selection.get("car_access_assumption"):
            st.caption("此驾车方案以你可以使用车辆为前提；若不方便驾车，请在需求中说明。")
    clarifications_form(result)
    with st.expander("已理解的出行需求",expanded=result.status=="needs_clarification"):
        req = result.request
        st.write({"起点":req.origin,"终点":req.destination,"方式":MODES.get(req.mode,"需澄清"),
            "出发时间":req.departure_time,"到达时限":req.arrival_deadline,"总时间上限（分钟）":req.max_duration_s/60 if req.max_duration_s else None,
            "经停":req.poi_name or req.poi_category,"必须经停":req.poi_required,"停留（分钟）":req.stop_duration_s/60 if req.stop_duration_s is not None else None,
            "避开高速":req.avoid_highways,"累计步行上限（米）":req.max_walking_m,
            "停车预留（分钟）":req.parking_duration_s/60 if req.parking_duration_s is not None else None,
            "避雨偏好":req.prefer_avoid_rain,"要求全程不淋雨":req.require_dry})
    routes = [result.recommended,*result.alternatives] if result.recommended else result.unverified
    if routes:
        st.subheader("推荐与备选" if result.recommended else "待核实方案")
        st.dataframe([{"方案":f"方案 {i+1}","方式":MODES[r.mode],"基础预计总时长":minutes(r.provider_total_s),
            "交通修正总时长":minutes(r.total_s) if r.mode!="walking" else "不适用（全程步行）","距离（公里）":round(r.distance_m/1000,2),
            "步行（米）":round(r.walking_m),"经停":r.poi.name if r.poi else "无",
            "交通覆盖":f"{r.traffic_coverage:.0%}" if r.traffic_coverage is not None else "不适用" if r.mode=="walking" else "未知",
            "状态":r.feasibility,"参考综合分":round(r.score,3) if r.score is not None else None} for i,r in enumerate(routes)],
            hide_index=True,use_container_width=True)
        selected = st.radio("选择方案",[r.id for r in routes],horizontal=True,key="route-"+result.run_id,
            format_func=lambda ident:next(f"方案 {i+1} · {MODES[r.mode]}" for i,r in enumerate(routes) if r.id==ident))
        route = next(r for r in routes if r.id==selected)
        cols = st.columns(3)+st.columns(2)
        for col,label,value in zip(cols,["基础预计总时长","交通修正总时长","行驶与步行距离","累计步行","LTA 交通覆盖"],
            [minutes(route.provider_total_s),minutes(route.total_s) if route.mode!="walking" else "不适用",
             f"{route.distance_m/1000:.2f} 公里",f"{route.walking_m:.0f} 米",
             f"{route.traffic_coverage:.0%}" if route.traffic_coverage is not None else "不适用" if route.mode=="walking" else "未知"]):
            col.metric(label,value)
        st.caption("基础总时长＝OSRM 行驶／步行服务时间＋用户确认的停留／停车预留；驾车部分未计入已核实实时路况。")
        for note in time_notes(route,result.context.get("traffic")):
            st.info(note)
        st.write(explain(route))
        with st.expander("DeepSeek 推荐依据与比较指标"):
            st.write("模型只能选择通过后端检查的候选；下面是工具计算的比较权重。")
            st.json({"权重":result.context.get("ranking_weights"),"引用证据":result.context.get("model_evidence_ids")})
        st.subheader("分段行程")
        st.dataframe([{"段":i+1,"方式":MODES[x.mode],"起点":x.origin.name,"终点":x.destination.name,
            "距离（米）":round(x.distance_m),"OSRM 基础时间":minutes(x.provider_duration_s),
            "LTA 时间估计":minutes(x.traffic_duration_s) if x.mode=="driving" else "不适用"} for i,x in enumerate(route.legs)],
            hide_index=True,use_container_width=True)
        map_view(routes,selected)
        if route.weather:
            st.subheader("天气预报参考")
            weather = route.weather
            if weather.status=="available":
                st.write(f"来源：{weather.source} · 关联时段：{weather.valid_start} — {weather.valid_end}")
                st.dataframe(weather.samples or weather.areas,hide_index=True,use_container_width=True)
            else:
                st.info("缺少覆盖采样点和行程时段的有效预报，天气保持未知。")
            st.caption(weather.mapping_basis+" 不能证明全程实际无雨，也不提供有遮蔽通道保证。")
            st.caption(weather.risk_basis)
            if weather.source=="Open-Meteo":
                st.caption("天气数据来源：[Open-Meteo](https://open-meteo.com/)。道路采样不表示预报具备街道级空间分辨率。")
        if route.parking:
            st.subheader("停车转换点")
            p = route.parking
            st.write(p.name)
            st.metric("查询时可用车位",str(p.available_lots) if p.available_lots is not None else "未知")
            st.caption("LTA 当前车位不是到达时的预约或保证；停车出入口需要用户核实。")
        st.subheader("逐项检查")
        st.dataframe([{"条件":LABELS.get(c.constraint,c.constraint),"状态":{"pass":"通过","fail":"违反","unknown":"待核实"}[c.status],
            "说明":c.reason,"证据 ID":", ".join(c.evidence_ids)} for c in route.checks],hide_index=True,use_container_width=True)
        if result.recommended and result.unverified:
            st.caption(f"另外有 {len(result.unverified)} 个待核实候选，不作为正式推荐。")
    elif result.explanation:
        st.write(result.explanation)
    with st.expander("真实工具调用与数据来源"):
        if result.context.get("nea_weather_supplement"):
            st.write("NEA 官方区域预报补充：独立于 Open-Meteo 网格预报，不证明逐路段无雨。")
            st.json(result.context["nea_weather_supplement"])
        roads = result.context.get("osm_roads",{})
        if roads:
            st.write("已查询的 OSM 道路属性：缺失标签保持未知，不代表实时通行状态。")
            st.dataframe([{"OSM ID":r["id"],"道路名":r["name"],**r["tags"]} for r in roads.values()],hide_index=True)
        for event in result.state.stages:
            st.write(f"{event['elapsed_s']:.1f}s · {event['message']}")
        if result.state.tools:
            st.dataframe([{"工具":t.tool,"状态":t.status,"说明":t.summary,"调用 ID":t.call_id,"复用":t.cached,
                "耗时（毫秒）":round(t.duration_ms),"证据":", ".join(t.evidence_ids)} for t in result.state.tools],
                hide_index=True,use_container_width=True)
        st.caption(f"重规划 {result.metrics.get('replans',0)} 次 · 模型 {result.metrics.get('model_calls',0)} 次 · 外部请求 {result.metrics.get('http_calls',0)} 次")
        st.json([e.model_dump(mode="json") for e in result.evidence])
    st.download_button("下载本次规划记录",result.model_dump_json(indent=2),file_name=f"route-v2-{result.run_id}.json",mime="application/json")

with st.sidebar:
    st.markdown("### 🧭 RouteAgent")
    st.caption("新加坡 · DeepSeek 真实在线规划")
    if st.button("刷新配置状态"):
        from dotenv import load_dotenv
        load_dotenv(ROOT/".env",override=True)
        st.rerun()
    settings = Settings()
    ready = settings.readiness()
    st.markdown("**API 配置状态**")
    for name,label,optional in [("DEEPSEEK_API_KEY","DeepSeek",False),("ORS_API_KEY","ORS 地址定位",False),
        ("LTA_API_KEY","LTA 交通与停车",False)]:
        st.write(("✅ " if ready[name] else "○ ")+label+"："+("已配置" if ready[name] else "可选，基础接口无需 Key" if optional else "缺少 Key"))
    st.caption("“已配置”只表示填写了 Key，不代表已验证服务可用。")
    routing = settings.routing_configuration()
    st.write("OSRM 算路：无需 Key；驾车／步行使用独立路网服务")
    st.caption("端点配置：驾车"+("已填写" if routing["driving_configured"] else "缺少")+"，步行"+("已填写" if routing["walking_configured"] else "缺少"))
    st.caption("公共 OSRM 仅供轻量试用，每秒最多一次；批量实验请配置自建服务。")
    if routing["public_driving_service"]:
        st.caption("当前公共服务不支持强制避高速；此类请求会提示更换算路服务。")
    st.write("OSM 道路／医院／餐厅／咖啡店：公开 Overpass 接口，无需 Key")
    st.write("Open-Meteo 天气：非商业公开接口，无需 Key")
    st.caption("NEA 可作为官方区域补充；data.gov.sg 配额 Key 可选。")
    st.divider()
    st.write("模型："+settings.model)
    st.caption("真实在线模式 · 思考与工具调用")
    st.caption("OSM 地图、道路与 POI · OSRM 算路 · LTA 路况与停车 · Open-Meteo 天气。模型和服务调用可能产生费用或消耗配额。")
    if not ready["LTA_API_KEY"]:
        st.info("配置 DeepSeek 和 ORS 地址定位后，全程步行无需 LTA；驾车实时路况与停车查询还需要 LTA Key。")

st.markdown('<div class="hero"><h1>说说你的出行需求</h1><p>直接描述你的行程，DeepSeek 理解需求、判断交通方式，再调用真实工具规划。</p></div>',unsafe_allow_html=True)
job = st.session_state.get("job")
busy = job is not None and not job.done()
missing = settings.missing_required()
if missing:
    st.warning("请先在项目根目录 .env 填写："+ "、".join(missing)+"。填写后点击左侧“刷新配置状态”。不会使用离线规则或演示结果替代。")
with st.form("request-form"):
    text = st.text_area("你的出行需求",key="request_text",height=140,
        placeholder="描述从哪里出发、要去哪里，以及时间、经停地点或其他偏好。交通方式可以由系统判断。")
    st.caption("无需选择示例或交通方式。未指定出发时间时，按现在出发规划；需要其他时间请写在需求里。")
    start = st.form_submit_button("开始在线规划",type="primary",disabled=busy or bool(missing))
if start:
    if text.strip():
        submit(text,{})
    else:
        st.warning("请先输入你的出行需求。")

@st.fragment(run_every="1s")
def monitor():
    job = st.session_state.get("job")
    if job is None:
        st.caption("首版支持驾车、全程步行和一次停车接驳，以及一个业务经停点。所有路线事实来自真实工具。")
        return
    events = st.session_state.events_queue
    while True:
        try:
            st.session_state.events.append(events.get_nowait())
        except queue.Empty:
            break
    if not job.done():
        message = st.session_state.events[-1]["message"] if st.session_state.events else "在线规划已提交"
        st.info("⏳ "+message)
        st.caption("任务在后台执行；进度为调用阶段，不代表确定剩余时间。")
        return
    if st.session_state.get("result") is None:
        try:
            st.session_state.result = job.result()
        except Exception:
            st.error("任务未完成，请检查服务配置后重新提交。")
            return
        st.rerun()

if st.session_state.get("result") is not None:
    render(st.session_state.result)
else:
    monitor()
