"""Public online service. Synthetic replay is not reachable through this entrypoint."""
import hashlib
import json
import time
import uuid
from datetime import datetime,timezone
from .config import Settings
from .models import TravelRequest,TaskState,PlanningResult
from .tools import Budget,ToolFailure
from .coordinator import Agent

def save_result(result,settings):
    directory = settings.root/"outputs"/result.run_id
    directory.mkdir(parents=True,exist_ok=True)
    (directory/"result.json").write_text(result.model_dump_json(indent=2),encoding="utf-8")
    (directory/"events.jsonl").write_text("\n".join(json.dumps(s,ensure_ascii=False,default=str) for s in result.state.stages)+"\n",encoding="utf-8")
    sources = sorted((settings.root/"src/route_agent").glob("*.py"))
    manifest = {"schema_version":2,"run_id":result.run_id,"model":settings.model,"thinking":settings.thinking,
        "mode":"live","metrics":result.metrics,"evidence_hashes":{e.id:e.response_sha256 for e in result.evidence},
        "code_sha256":{str(p.relative_to(settings.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    (directory/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")

def plan_route(text,clarifications=None,*,settings=None,progress=None,save=True,_client=None,_providers=None):
    settings = settings or Settings()
    budget = Budget(settings)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:8]
    request = TravelRequest(original_text=text)
    result = PlanningResult(run_id=run_id,status="tool_error",message="规划尚未完成。",request=request,state=TaskState(request=request),
        context={"mode":"live","provider":"DeepSeek","model":settings.model,"readiness":settings.readiness(),
                 "routing":settings.routing_configuration()})
    agent = None
    def stage(name,message,**extra):
        event = {"stage":name,"message":message,"elapsed_s":round(time.monotonic()-budget.started,3),**extra}
        result.state.stages.append(event)
        if progress:
            progress(event)
    try:
        if settings.mode!="live":
            raise ToolFailure("正常规划仅支持真实在线模式；历史快照仅用于独立测试。","invalid_mode")
        if settings.thinking!="enabled":
            raise ToolFailure("本项目要求 DeepSeek 思考模式，请设置 DEEPSEEK_THINKING=enabled。","configuration_error")
        if not text.strip():
            raise ToolFailure("请先填写出行需求。","empty_request")
        missing = settings.missing_required()
        if missing:
            raise ToolFailure("请先在根目录 .env 配置："+ "、".join(missing)+"。没有使用演示数据或离线规则。","missing_credential")
        agent = Agent(result,settings,budget,stage,clarifications,providers=_providers)
        agent.run(_client)
    except Exception as error:
        result.status = "tool_error"
        result.message = str(error) if isinstance(error,ToolFailure) else "规划未能完成，请检查输入或稍后重试。"
        result.context["error_code"] = error.code if isinstance(error,ToolFailure) else "unexpected_error"
        result.context["error_type"] = type(error).__name__
        stage("error",result.message)
        # Diagnostics deliberately omit exception messages, locals, headers and raw reasoning.
        if save:
            directory = settings.root/"outputs"/run_id
            directory.mkdir(parents=True,exist_ok=True)
            import traceback
            frames = [{"file":f.filename,"line":f.lineno,"function":f.name} for f in traceback.extract_tb(error.__traceback__)]
            (directory/"diagnostic.json").write_text(json.dumps({"error_type":type(error).__name__,"frames":frames},indent=2),encoding="utf-8")
    finally:
        if agent:
            result.evidence = list(agent.providers.evidence.values())
            result.state.candidates = list(agent.routes.values())
        result.metrics = {"latency_s":round(time.monotonic()-budget.started,3),"http_calls":budget.http_calls,
            "tool_calls":budget.tool_calls,"model_calls":agent.model_calls if agent else 0,
            "model_tokens":agent.model_tokens if agent else 0,"replans":max(0,len(result.state.attempts)-1),
            "candidate_count":len(result.state.candidates),"cache_hits":agent.providers.cache_hits if agent else 0,
            "model":settings.model,"live_acceptance":"not established by automated/mocked tests"}
        if save:
            save_result(result,settings)
    return result
