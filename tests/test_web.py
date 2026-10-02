"""UI integration with mock results; no real model/provider connectivity claimed."""
import socket
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from streamlit.testing.v1 import AppTest
import route_agent
from route_agent.config import ROOT
from route_agent.models import TravelRequest, TaskState, PlanningResult, Place, RouteLeg, WeatherContext
from route_agent.routing import assemble
from route_agent.verification import verify_route

@pytest.fixture(autouse=True)
def controlled_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY","test-ui-key")
    monkeypatch.setenv("ORS_API_KEY","test-ui-key")
    original_connect=socket.socket.connect
    def no_network(sock,address):
        if isinstance(address,tuple) and address[0] in ("127.0.0.1","::1","localhost"):
            return original_connect(sock,address)
        raise AssertionError("UI tests cannot call real APIs")
    monkeypatch.setattr(socket.socket,"connect",no_network)

def button(app,label):
    return next(b for b in app.button if b.label==label)

def wait_for_job(app):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if app.session_state["job"].done():
            app.run(timeout=15)
            if app.session_state["result"] is not None: return app
        time.sleep(.02)
    raise AssertionError("Mock job did not finish")

def result_for(text,clarifications=None,**kwargs):
    req=TravelRequest(original_text=text,origin="City Hall",destination="Marina Bay",mode="walking",
        departure_time=datetime.now(ZoneInfo("Asia/Singapore")))
    result=PlanningResult(run_id="ui-mock",status="verified",message="MOCK 页面测试方案",request=req,state=TaskState(request=req))
    a=Place(id="a",name="City Hall",lat=1.29,lon=103.85,source="MOCK")
    b=Place(id="b",name="Marina Bay",lat=1.30,lon=103.86,source="MOCK")
    leg=RouteLeg(id="leg",mode="walking",origin=a,destination=b,distance_m=1000,provider_duration_s=600,
        geometry={"type":"LineString","coordinates":[[a.lon,a.lat],[b.lon,b.lat]]})
    result.recommended=assemble([leg],req,a,b)
    verify_route(result.recommended,req)
    result.metrics={"replans":1}
    return result

def test_web_missing_keys_no_demo_or_parser_choices(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.delenv("ORS_API_KEY")
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    assert not app.exception
    assert button(app,"开始在线规划").disabled
    assert any(".env" in warning.value for warning in app.warning)
    assert all("快照" not in box.label and "解析" not in box.label for box in app.selectbox)

def test_web_result_map_export_and_resubmit(monkeypatch):
    calls=[]
    def planner(*a,**kw):
        calls.append(a[0]); return result_for(*a,**kw)
    monkeypatch.setattr(route_agent,"plan_route",planner)
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    app.text_area[0].set_value("从 City Hall 步行到 Marina Bay")
    button(app,"开始在线规划").click().run()
    app=wait_for_job(app)
    assert not app.exception
    assert app.metric[0].value=="10.0 分钟"
    assert app.get("download_button")
    assert any("逐项检查"==x.value for x in app.subheader)
    assert app.get("component_instance")
    app.text_area[0].set_value("修改后的步行需求")
    button(app,"开始在线规划").click().run()
    app=wait_for_job(app)
    assert calls[-1]=="修改后的步行需求" and not app.exception

def test_web_clarification_then_continue(monkeypatch):
    received=[]
    def planner(text,clarifications=None,**kwargs):
        received.append(clarifications or {})
        result=result_for(text)
        if "parking_duration_s" not in (clarifications or {}):
            result.status="needs_clarification"; result.recommended=None
            result.questions={"parking_duration_s":"停车及转换预留多少分钟？"}
        return result
    monkeypatch.setattr(route_agent,"plan_route",planner)
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    app.text_area[0].set_value("从 City Hall 到 Marina Bay，停车后步行")
    button(app,"开始在线规划").click().run()
    app=wait_for_job(app)
    assert app.number_input and not app.exception
    app.number_input[0].set_value(5.0)
    button(app,"确认并继续在线规划").click().run()
    app=wait_for_job(app)
    assert received[-1]["parking_duration_s"]==300
    assert app.session_state["result"].status=="verified" and not app.exception

def test_web_service_error_is_visible(monkeypatch):
    def planner(text,*a,**kw):
        result=result_for(text); result.recommended=None; result.status="tool_error"; result.message="真实模型服务不可用"
        return result
    monkeypatch.setattr(route_agent,"plan_route",planner)
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    app.text_area[0].set_value("从 City Hall 步行到 Marina Bay")
    button(app,"开始在线规划").click().run()
    app=wait_for_job(app)
    assert any("真实模型服务不可用" in e.value for e in app.error)
    assert not app.metric and app.get("download_button") and not app.exception

def test_web_user_hospital_request_weather_samples_and_osm_roads(monkeypatch):
    def planner(text,*a,**kw):
        result=result_for(text)
        result.recommended.weather=WeatherContext(status="available",valid_start=result.request.departure_time,
            valid_end=result.request.departure_time+timedelta(minutes=10),rain_fraction=.2,
            samples=[{"采样经纬度":"[103.85,1.29]","降水概率（%）":20,"降水量（mm）":0.1}])
        result.context["osm_roads"]={"osm:way:1":{"id":"osm:way:1","name":"Mock Road","tags":{"oneway":"yes","maxspeed":None}}}
        return result
    monkeypatch.setattr(route_agent,"plan_route",planner)
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    assert not app.selectbox and app.text_area[0].value==""
    app.text_area[0].set_value("从 City Hall 到 Marina Bay，经过医院")
    assert "医院" in app.text_area[0].value
    button(app,"开始在线规划").click().run()
    app=wait_for_job(app)
    assert not app.exception and any(s.value=="天气预报参考" for s in app.subheader)
    assert any("Open-Meteo" in c.value for c in app.caption)
    assert any("OSM 道路属性" in m.value for m in app.markdown)

def test_web_blank_request_does_not_submit_and_has_no_presets(monkeypatch):
    calls=[]
    monkeypatch.setattr(route_agent,"plan_route",lambda *a,**kw:calls.append(a))
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    assert app.text_area[0].value=="" and not app.selectbox and not app.radio
    assert not app.get("datetime_input")
    button(app,"开始在线规划").click().run()
    assert not calls and any("请先输入" in w.value for w in app.warning)

def test_web_request_is_not_overridden_by_form_mode_or_time(monkeypatch):
    received=[]
    def planner(text,clarifications=None,**kwargs):
        received.append((text,clarifications)); return result_for(text)
    monkeypatch.setattr(route_agent,"plan_route",planner)
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    text="明天上午从 City Hall 到 Marina Bay，请帮我判断怎样出行"
    app.text_area[0].set_value(text)
    button(app,"开始在线规划").click().run()
    app=wait_for_job(app)
    assert received==[(text,{})] and not app.exception

def test_web_shows_base_total_when_traffic_is_unavailable(monkeypatch):
    def planner(text,*a,**kw):
        result=result_for(text)
        route=result.recommended
        route.mode="driving"; route.legs[0].mode="driving"
        route.total_s=None; route.traffic_coverage=0
        result.request.mode="driving"; result.request.max_duration_s=1800
        verify_route(route,result.request)
        result.status="unverified"; result.unverified=[route]; result.recommended=None
        result.context["traffic"]={"missing":"MOCK 交通查询未完成"}
        return result
    monkeypatch.setattr(route_agent,"plan_route",planner)
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    app.text_area[0].set_value("现在从 City Hall 驾车到 Marina Bay，30 分钟内到达")
    button(app,"开始在线规划").click().run()
    app=wait_for_job(app)
    assert not app.exception
    assert app.metric[0].label=="基础预计总时长" and app.metric[0].value=="10.0 分钟"
    assert app.metric[1].label=="交通修正总时长" and app.metric[1].value=="尚未核实"
    assert any("MOCK 交通查询未完成" in note.value for note in app.info)

def test_place_confirmation_has_no_default_and_allows_replacing_query(monkeypatch):
    received=[]
    def planner(text,clarifications=None,**kwargs):
        received.append(clarifications or {})
        result=result_for(text)
        result.status="needs_clarification"; result.recommended=None
        result.questions={"destination_place_id":"请选择正确的终点。"}
        result.context["choices"]={"destination_place_id":[{"id":"mbs","name":"Marina Bay Sands"}]}
        return result
    monkeypatch.setattr(route_agent,"plan_route",planner)
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    app.text_area[0].set_value("从 City Hall 到滨海湾金沙")
    button(app,"开始在线规划").click().run()
    app=wait_for_job(app)
    assert app.selectbox[0].value is None
    button(app,"确认并继续在线规划").click().run()
    assert len(received)==1
    assert any("不会默认" in w.value for w in app.warning)
    app.text_input[0].set_value("Marina Bay Sands")
    button(app,"确认并继续在线规划").click().run()
    app=wait_for_job(app)
    assert received[-1]["destination"]=="Marina Bay Sands"
    assert "destination_place_id" not in received[-1]
    assert not app.exception

def test_place_continuation_preserves_validated_poi_constraints(monkeypatch):
    received=[]
    def planner(text,clarifications=None,**kwargs):
        received.append(clarifications or {})
        result=result_for(text)
        result.request.poi_category="cafe"; result.request.poi_required=True; result.request.stop_duration_s=600
        if len(received)==1:
            result.status="needs_clarification"; result.recommended=None
            result.questions={"destination_place_id":"请选择正确的终点。"}
            result.context["choices"]={"destination_place_id":[{"id":"mbs","name":"Marina Bay Sands"}]}
        return result
    monkeypatch.setattr(route_agent,"plan_route",planner)
    app=AppTest.from_file(str(ROOT/"app/streamlit_app.py")).run(timeout=20)
    app.text_area[0].set_value("到金沙，经过咖啡店停留10分钟")
    button(app,"开始在线规划").click().run()
    app=wait_for_job(app)
    app.selectbox[0].select("mbs")
    button(app,"确认并继续在线规划").click().run()
    app=wait_for_job(app)
    assert received[-1]["poi_category"]=="cafe"
    assert received[-1]["poi_required"] is True and received[-1]["stop_duration_s"]==600
    assert received[-1]["destination_place_id"]=="mbs"
    assert not app.exception
