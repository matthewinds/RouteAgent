import json
from dataclasses import replace
from types import SimpleNamespace
from route_agent.fixtures.config import Settings
from route_agent.fixtures.parser import parse_request
from route_agent.fixtures.coordinator import decide


def fake_model(monkeypatch, payload):
    import openai
    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
        def create(self,**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],usage=None)
    monkeypatch.setattr(openai,"OpenAI",Client)
    monkeypatch.setenv("OPENROUTER_API_KEY","test-placeholder")


def test_invalid_llm_schema_falls_back_to_rules(monkeypatch):
    fake_model(monkeypatch,{"avoid_highways":"hard","run_code":"forbidden"})
    req,questions,meta = parse_request("现在驾车从 City Hall 到 Orchard Road，不能走高速。",
        {"departure_time":"2026-10-01T09:00:00+08:00"},settings=replace(Settings(),parser="llm"))
    assert req.avoid_highways and not questions and meta["parser"]=="rules" and meta["warnings"]


def test_llm_cannot_skip_evidence_or_expand_unavailable_pool(monkeypatch):
    fake_model(monkeypatch,{"fetch_traffic":False,"strategies":["fastest"],"action":"expand_pois","reason":"test"})
    req,_,_ = parse_request("现在驾车从 City Hall 到 Orchard Road，30 分钟内到达。")
    decision,meta = decide(req,replace(Settings(),parser="llm"),need_traffic=True,
        failures=["duration"],can_expand=False,can_add_paths=True)
    assert decision.fetch_traffic and decision.action=="more_alternatives" and meta["coordinator"]=="llm"
