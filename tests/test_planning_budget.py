"""Budget and failure regressions; mocked services are not live acceptance."""
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError, APIConnectionError, APIStatusError

from route_agent.config import Settings
from route_agent.models import Place
from route_agent.providers import Providers
from route_agent.service import plan_route
from route_agent.tools import Budget, ToolFailure
from test_online import make_agent, FakeProviders, Model, deny_network


@pytest.mark.parametrize("status,code",[(401,"model_authentication_error"),(402,"model_balance_error"),
    (403,"model_authentication_error"),(429,"model_rate_limited"),(503,"model_error")])
def test_model_http_failures_keep_only_safe_metadata(tmp_path,status,code):
    request=httpx.Request("POST","https://api.deepseek.com/chat/completions")
    error=APIStatusError("test-secret-deepseek",response=httpx.Response(status,request=request),
                         body={"private":"test-secret-deepseek"})
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:(_ for _ in ()).throw(error))))
    result=plan_route("测试",settings=Settings(root=tmp_path),_client=client,_providers=FakeProviders())
    assert result.context["error_code"]==code
    assert result.context["model_failure"]["http_status"]==status
    assert "test-secret" not in "".join(p.read_text() for p in (tmp_path/"outputs"/result.run_id).glob("*"))


@pytest.mark.parametrize("kind,expired,code",[("timeout",False,"model_timeout"),
    ("timeout",True,"budget_exhausted"),("connection",False,"model_network_error")])
def test_model_timeout_is_distinct_from_expired_total_budget(tmp_path,monkeypatch,kind,expired,code):
    clock=[100.0]
    monkeypatch.setattr("route_agent.tools.time.monotonic",lambda:clock[0])
    req=httpx.Request("POST","https://api.deepseek.com/chat/completions")
    def create(**kwargs):
        clock[0]+=241 if expired else 5
        raise APITimeoutError(request=req) if kind=="timeout" else APIConnectionError(request=req)
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result=plan_route("测试",settings=Settings(root=tmp_path),_client=client,_providers=FakeProviders(),save=False)
    assert result.context["error_code"]==code
    assert result.context["model_failure"]["request_timeout_s"]==60


def test_search_deadline_limits_network_retries_and_restores_completion_time(tmp_path,monkeypatch):
    clock=[100.0]
    monkeypatch.setattr("route_agent.tools.time.monotonic",lambda:clock[0])
    settings=Settings(root=tmp_path)
    budget=Budget(settings);provider=Providers(settings,budget);calls=[]
    def request(self,method,url,**kwargs):
        calls.append(self.timeout.read)
        clock[0]+=self.timeout.read
        raise httpx.ReadTimeout("secret must not be exported")
    monkeypatch.setattr(httpx.Client,"request",request)
    with budget.search():
        with pytest.raises(ToolFailure) as error:provider.request("GET","https://example.com","MOCK")
        assert error.value.code=="search_budget"
    assert calls==[20,20,5] and budget.remaining()==195
    assert budget.search_deadline is None
    budget.http()  # Verification/model requests can use the restored budget.


def test_batch_keeps_completed_candidates_and_can_explore_another_mode(tmp_path,monkeypatch):
    agent=make_agent(tmp_path,mode=None,poi_required=True,poi_category="cafe",stop_duration_s=0)
    clock=[agent.budget.started]
    monkeypatch.setattr("route_agent.tools.time.monotonic",lambda:clock[0])
    pois=[Place(id=f"poi{i}",name="Cafe",lat=1.295+i*.0001,lon=103.855,
                category="cafe",source="MOCK",evidence_ids=["geo"]) for i in range(3)]
    agent.places.update({p.id:p for p in pois})
    original=agent.providers.routes
    def routes(*args):
        agent.budget.check_search()  # As the real provider does before each request.
        clock[0]+=12
        return original(*args)
    agent.providers.routes=routes
    output=agent.plan_candidates(poi_ids=[p.id for p in pois],modes=["walking"])
    assert len(output["route_ids"])==2
    assert agent.result.state.attempts[-1]["failures"][-1]["error"]=="search_budget"
    assert all(agent.routes[x].source_warnings for x in output["route_ids"])
    agent.validate_routes()
    second=agent.plan_candidates(poi_ids=[pois[2].id],modes=["walking"])
    assert second["route_ids"] and agent.budget.search_deadline is None


def test_completion_stage_blocks_new_search_and_model_sees_remaining_budget(tmp_path,monkeypatch):
    agent=make_agent(tmp_path);agent.plan_candidates()
    clock=[agent.budget.started+190]
    monkeypatch.setattr("route_agent.tools.time.monotonic",lambda:clock[0])
    output=agent.execute("search_pois",json.dumps({}),"late-search")
    assert output["error"]=="search_budget"
    agent.validate_routes()
    route=next(iter(agent.routes.values()))
    model=Model([("finish_plan",{"selected_id":route.id,"reason_codes":["walking"]})])
    agent.run(model)
    assert agent.result.status=="verified"
    budget=json.loads(model.seen[0]["messages"][-1]["content"])["planning_budget"]
    assert "收尾阶段" in budget["next_step"] and budget["remaining_seconds"]==50


def test_late_mode_attempt_records_search_limit_and_allows_honest_completion(tmp_path,monkeypatch):
    agent=make_agent(tmp_path,mode=None,prefer_fastest=True)
    agent.plan_candidates(modes=["walking"])
    clock=[agent.budget.started+190]
    monkeypatch.setattr("route_agent.tools.time.monotonic",lambda:clock[0])
    output=agent.execute("plan_candidates",json.dumps({"modes":["transit"]}),"late-transit")
    assert output["error"]=="search_budget"
    comparison=agent.result.context["candidate_mode_comparison"]
    assert comparison["attempted_modes"]==["walking","transit"]
    assert comparison["failures"][0]["error"]=="search_budget"
    route=next(iter(agent.routes.values()))
    output=agent.execute("finish_plan",json.dumps({"selected_id":route.id}),"limited-finish")
    assert output["status"]=="unverified" and agent.result.recommended is not None
    assert any("比较尚不完整" in gap for gap in agent.result.context["unmet_preferences"])
