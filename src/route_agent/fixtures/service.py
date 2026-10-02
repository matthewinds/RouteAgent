"""Public planning service, shared by the web app, CLI, and evaluation harness."""
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .config import Settings
from .models import PlanningResult, TaskState, TravelRequest
from .parser import parse_request
from .maps import load_context
from .tools import ToolFailure, ToolRegistry
from .traffic import apply_traffic
from .routing import generate_candidates, routing_graph, shortest_paths
from .verification import verify_route
from .ranking import rank_routes, explain
from .coordinator import decide

POLICIES = {"full", "fastest", "fixed", "no_verification", "no_replanning", "no_personalization", "no_coarse"}


def _write_run(result, settings):
    directory = settings.root / "outputs" / result.run_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (directory / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False, default=str)
        for e in result.state.stages) + "\n", encoding="utf-8")
    import importlib.metadata
    code = sorted((settings.root / "src/route_agent").glob("*.py"))
    manifest = {"run_id": result.run_id, "created_utc": datetime.now(timezone.utc).isoformat(),
        "context": result.context, "metrics": result.metrics, "model": settings.model,
        "parser": result.context.get("parser"), "policy": result.metrics.get("policy"),
        "packages": {name: importlib.metadata.version(name) for name in ("networkx", "pydantic", "osmnx", "streamlit")},
        "code_sha256": {str(p.relative_to(settings.root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code}}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def plan_route(text, clarifications=None, context_mode="replay", snapshot_id="demo-clear", *,
               settings=None, progress=None, policy="full", save=True):
    settings = settings or Settings()
    if policy not in POLICIES:
        raise ValueError("Unknown evaluation policy")
    start = time.monotonic()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    request = TravelRequest(original_text=text)
    state = TaskState(request=request)
    result = PlanningResult(run_id=run_id, status="tool_error", message="规划尚未完成。", request=request, state=state)

    def stage(name, message, **extra):
        event = {"stage": name, "message": message, "elapsed_s": round(time.monotonic()-start, 3), **extra}
        state.stages.append(event)
        if progress:
            progress(event)

    try:
        stage("parse", "正在理解出行需求")
        request, questions, parse_meta = parse_request(text, clarifications, settings=settings)
        state.request = request
        result.request = request
        result.context = parse_meta
        if questions:
            result.status, result.questions = "needs_clarification", questions
            result.message = "请补充或确认以下信息后继续规划。"
            return result
        stage("context", "正在加载路网与出行信息")
        context = load_context(context_mode, snapshot_id, settings)
        result.context.update(context.metadata)
        registry = ToolRegistry(state, settings.max_tool_calls, settings.max_seconds)
        registry.register("geocode", context.adapter.geocode)
        registry.register("search_pois", context.adapter.search_pois)
        registry.register("traffic", context.adapter.traffic_data)
        for field in ("origin", "destination"):
            try:
                place = registry.call("geocode", query=getattr(request, field))
            except ToolFailure as error:
                result.status = "needs_clarification"
                result.questions[field] = str(error)
                result.message = "请确认地点名称或道路入口。"
                return result
            if field == "origin":
                origin = place
            else:
                destination = place
        wants_poi = bool(request.poi_category or request.poi_name)
        pois = registry.call("search_pois", category=request.poi_category, name=request.poi_name) if wants_poi else []
        if request.poi_name and len(pois) == 1:
            request.resolved_poi_id = pois[0].id
        if policy == "fixed" and not wants_poi:
            registry.call("search_pois", category="cafe", name=None)
        needs_traffic = (request.max_duration_s is not None or request.arrival_deadline is not None or
                         request.prefer_avoid_congestion or request.max_congestion_fraction is not None or request.require_open)
        decision, decision_meta = decide(request,settings,need_traffic=needs_traffic)
        stage("planning", decision.reason, decision=decision.model_dump(), **decision_meta)
        if decision.fetch_traffic or policy == "fixed":
            stage("traffic", "正在核对交通证据与道路方向")
            try:
                payload = registry.call("traffic")
                traffic_meta = apply_traffic(context.graph, payload, request.departure_time,
                    replay=context_mode == "replay", max_age_s=settings.traffic_max_age_s)
                context.traffic = payload
            except Exception as error:
                # Use free-flow estimates for display; a hard deadline becomes
                # unknown through missing coverage, never a false verified pass.
                traffic_meta = {"source": "unavailable", "fresh": False, "matched_edges": 0,
                                "error_type": type(error).__name__}
        else:
            traffic_meta = {"source": "not requested", "matched_edges": 0, "fresh": False}
        context.metadata["traffic"] = traffic_meta
        result.context["traffic"] = traffic_meta
        if context_mode == "live":
            # Public map records and traffic are sufficient for subsequent replay.
            snapshot = {"metadata": {**context.metadata, "mode": "replay"},
                "graph_file": "data/osm/singapore.graphml", "graph_sha256": context.metadata["graph_sha256"],
                "places": [p.model_dump() for p in {p.id:p for p in [origin, destination, *pois]}.values()],
                "traffic": context.traffic or {}}
            settings.snapshots.mkdir(parents=True, exist_ok=True)
            (settings.snapshots / f"captured-{run_id}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            result.context["captured_snapshot"] = f"captured-{run_id}"
        reference = routing_graph(context.graph, request, "base_fastest")
        ref_paths = shortest_paths(reference, origin.node, destination.node, 1)
        ref_time = sum(context.graph[u][v][reference[u][v]["original_key"]]["base_time_s"]
                       for u, v in zip(ref_paths[0], ref_paths[0][1:])) if ref_paths else 1
        distance_graph = routing_graph(context.graph, request, "shortest")
        ref_distance_paths = shortest_paths(distance_graph, origin.node, destination.node, 1)
        ref_distance = sum(context.graph[u][v][distance_graph[u][v]["original_key"]]["length"]
            for u, v in zip(ref_distance_paths[0], ref_distance_paths[0][1:])) if ref_distance_paths else 1
        limit, path_count = settings.first_pois, settings.paths_per_leg
        max_attempt = 0 if policy in {"fastest", "fixed", "no_replanning", "no_verification"} else settings.max_replans
        attempted = set()
        all_routes = {}
        for attempt in range(max_attempt+1):
            if time.monotonic()-start > settings.max_seconds:
                raise ToolFailure("规划已达到时间预算，请缩小搜索范围后重试。")
            stage("candidates" if attempt == 0 else "replan", "正在计算候选路线" if attempt == 0 else "根据检查结果重新规划", attempt=attempt)
            signature = (limit, path_count)
            if signature in attempted:
                break
            attempted.add(signature)
            route_request = request
            strategies = decision.strategies
            if policy == "fastest":
                route_request = request.model_copy(update={"poi_category": None, "poi_name": None, "poi_required": False,
                    "prefer_avoid_congestion": False, "prefer_avoid_highways": False, "avoid_highways": False})
                strategies, path_count = ["base_fastest"], 1
            routes, search = generate_candidates(context.graph, origin, destination, pois, route_request, context.metadata,
                poi_limit=limit, k=path_count, coarse_filter=policy != "no_coarse", strategies=strategies)
            stage("verify", "正在逐项检查原始出行约束", count=len(routes))
            for route in routes:
                if policy == "no_verification":
                    route.feasibility = "unverified"
                else:
                    verify_route(route, request)
                all_routes[route.id] = route
            state.candidates = list(all_routes.values())
            state.search = search
            failures = sorted({c.constraint for r in routes for c in r.checks if c.status != "pass"})
            state.attempts.append({"attempt": attempt, "poi_limit": limit, "paths_per_leg": path_count,
                "generated": len(routes), "verified": sum(r.feasibility == "verified" for r in routes),
                "failed_checks": failures, "search": search})
            valid = [r for r in state.candidates if r.feasibility == "verified"]
            if valid or policy == "no_verification":
                break
            can_expand = wants_poi and len(pois) > limit
            can_add_paths = path_count == settings.paths_per_leg and (not routes or any(x in failures for x in ("duration", "arrival_deadline", "congestion")))
            # Do not perform another model call if there is no attempt budget.
            if attempt >= max_attempt:
                state.attempts[-1]["next_action"] = "stop_budget"
                break
            decision,decision_meta = decide(request,settings,failures=failures,can_expand=can_expand,can_add_paths=can_add_paths)
            stage("planning",decision.reason,decision=decision.model_dump(),**decision_meta)
            if decision.action == "expand_pois":
                limit = settings.expanded_pois
                state.attempts[-1]["next_action"] = "expand_pois"
            elif decision.action == "more_alternatives":
                path_count = 5
                state.attempts[-1]["next_action"] = "more_alternative_paths"
            else:
                state.attempts[-1]["next_action"] = "stop_no_new_search"
                break
        stage("rank", "正在比较方案并生成证据说明")
        valid = [r for r in state.candidates if r.feasibility == "verified"]
        pending = [r for r in state.candidates if r.feasibility == "unverified"]
        ranked = rank_routes(valid, request, ref_time, ref_distance, personalized=policy != "no_personalization")
        pending = rank_routes(pending, request, ref_time, ref_distance, personalized=policy != "no_personalization")
        result.unverified = pending[:3]
        if ranked:
            result.status, result.message = "verified", "已找到满足当前模型下全部硬约束的路线。"
            result.recommended, result.alternatives = ranked[0], ranked[1:3]
            result.explanation = explain(ranked[0])
        elif pending:
            result.status, result.message = "unverified", "有候选方案，但部分硬约束尚未核实。"
            result.explanation = explain(pending[0])
        else:
            result.status, result.message = "search_exhausted", "在当前搜索范围内未找到满足要求的路线。"
            reasons = {c.reason for r in state.candidates for c in r.checks if c.status == "fail"}
            result.explanation = "；".join(sorted(reasons)) or "请核对地点与路网范围，或主动修改出行条件后重试。"
        stage("complete", result.message)
        return result
    except Exception as error:
        result.status = "tool_error"
        result.message = str(error) if isinstance(error, ToolFailure) else "规划未能完成，请检查输入或稍后重试。"
        result.context["error_type"] = type(error).__name__
        error_dir = settings.root / "outputs" / run_id
        error_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(error_dir / "diagnostic.log", encoding="utf-8")
        logger = logging.getLogger("route_agent."+run_id)
        logger.addHandler(handler)
        logger.error("Planning failure", exc_info=True)
        logger.removeHandler(handler)
        handler.close()
        stage("error", result.message)
        return result
    finally:
        result.state = state
        usages = [result.context.get("parser_usage", {})] + [event.get("usage", {})
            for event in state.stages if event.get("coordinator") == "llm"]
        result.metrics = {"latency_s": round(time.monotonic()-start, 4), "policy": policy,
            "tool_calls": len([t for t in state.tools if not t.cached]), "cache_hits": sum(t.cached for t in state.tools),
            "attempts": len(state.attempts), "replans": max(0, len(state.attempts)-1),
            "candidate_count": len(state.candidates), "model": settings.model if settings.parser == "llm" else None,
            "model_calls": int(result.context.get("parser") == "llm") + sum(
                event.get("coordinator") == "llm" for event in state.stages),
            "model_tokens": sum(usage.get("total_tokens", 0) or 0 for usage in usages),
            "reported_model_cost_usd": sum(usage.get("cost", 0) or 0 for usage in usages)
                if any(usage.get("cost") is not None for usage in usages) else None}
        if save:
            _write_run(result, settings)
