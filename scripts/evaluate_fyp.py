"""Controlled SYNTHETIC mechanism evaluation, not real-world route validation."""
import argparse
import csv
import json
import statistics
import hashlib
import importlib.metadata
from datetime import datetime, timezone
from pathlib import Path
from route_agent.fixtures import plan_route
from route_agent.fixtures.config import ROOT
from route_agent.fixtures.config import Settings
from route_agent.fixtures.service import POLICIES
from route_agent.fixtures.verification import verify_route


def evaluate(split="test",repeat=1,parser_mode="rules"):
    settings = Settings(parser=parser_mode)
    cases = json.loads((ROOT/f"data/requests/{split}.json").read_text(encoding="utf-8"))
    directory = ROOT/"outputs"/("evaluation-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    directory.mkdir(parents=True,exist_ok=True)
    rows, results = [], []
    for policy in sorted(POLICIES):
        for case in cases:
            for iteration in range(repeat):
                result = plan_route(case["text"],case["clarifications"],snapshot_id=case["snapshot_id"],policy=policy,settings=settings,save=False)
                # Independent scoring always uses the original request, even for
                # no-verification/fastest variants. Failure output is not success.
                ranked = result.recommended or (result.unverified[0] if result.unverified else None)
                if ranked is None and result.state.candidates and policy == "fastest":
                    ranked = min(result.state.candidates,key=lambda x:x.total_s)
                truth = verify_route(ranked.model_copy(deep=True),result.request) if ranked else None
                eligible = case["expected_status"] not in {"needs_clarification","search_exhausted","unverified"}
                gold = case["gold_request"]
                prediction = result.request.model_dump()
                row = {"case_id":case["id"],"policy":policy,"repeat":iteration,"group":case["group"],
                    "expected_status":case["expected_status"],"status":result.status,
                    "status_correct":result.status == case["expected_status"],
                    "eligible_feasible":eligible,"satisfied":bool(truth and truth.feasibility=="verified"),
                    "unknown":bool(truth and truth.feasibility=="unverified"),
                    "recovered":bool(result.state.attempts and result.state.attempts[0]["verified"]==0 and result.recommended),
                    "first_failed":bool(result.state.attempts and result.state.attempts[0]["verified"]==0),
                    "latency_s":result.metrics["latency_s"],"tool_calls":result.metrics["tool_calls"],
                    "replans":result.metrics["replans"],"candidates":result.metrics["candidate_count"],
                    "parser_field_accuracy":statistics.mean(prediction[key] == value for key,value in gold.items()),
                    "model_calls":result.metrics["model_calls"],"model_tokens":result.metrics["model_tokens"],
                    "score":ranked.score if ranked else None,"time_s":ranked.total_s if ranked else None}
                rows.append(row)
                results.append({"case":case,"repeat":iteration,"result":result.model_dump(mode="json")})
        print(f"Finished {policy}")
    with (directory/"records.jsonl").open("w",encoding="utf-8") as stream:
        for record in results:
            stream.write(json.dumps(record,ensure_ascii=False)+"\n")
    with (directory/"metrics.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    summary = {}
    for policy in sorted(POLICIES):
        subset = [x for x in rows if x["policy"]==policy]
        feasible = [x for x in subset if x["eligible_feasible"]]
        first_fail = [x for x in feasible if x["first_failed"]]
        summary[policy] = {"count":len(subset),"status_accuracy":statistics.mean(x["status_correct"] for x in subset),
            "constraint_satisfaction_all":statistics.mean(x["satisfied"] for x in subset),
            "feasible_found_rate":statistics.mean(x["satisfied"] for x in feasible) if feasible else None,
            "replanning_recovery_rate":statistics.mean(x["recovered"] for x in first_fail) if first_fail else None,
            "unknown_rate":statistics.mean(x["unknown"] for x in subset),
            "mean_latency_s":statistics.mean(x["latency_s"] for x in subset),
            "mean_tool_calls":statistics.mean(x["tool_calls"] for x in subset),
            "mean_candidates":statistics.mean(x["candidates"] for x in subset)}
        summary[policy]["parser_field_accuracy"] = statistics.mean(x["parser_field_accuracy"] for x in subset)
        summary[policy]["mean_model_calls"] = statistics.mean(x["model_calls"] for x in subset)
    # Candidate recall compares full vs no-coarse on the SAME limited route search;
    # it is not proof of completeness on the road network.
    recall, regret = [], []
    for case in cases:
        records = [r for r in results if r["case"]["id"]==case["id"] and r["repeat"]==0]
        by_policy = {r["result"]["metrics"]["policy"]:r["result"] for r in records}
        a = {r["id"] for r in by_policy["full"]["state"]["candidates"] if r["feasibility"]=="verified"}
        b = {r["id"] for r in by_policy["no_coarse"]["state"]["candidates"] if r["feasibility"]=="verified"}
        if b:
            recall.append(len(a & b)/len(b))
        reference = by_policy["no_coarse"]["recommended"]
        chosen = by_policy["full"]["recommended"]
        if reference and chosen:
            regret.append(max(0,chosen["score"]-reference["score"]))
    report = {"split":split,"repeats":repeat,"synthetic":True,"parser":parser_mode,"model":settings.model if parser_mode=="llm" else None,"summary":summary,
        "request_sha256":hashlib.sha256((ROOT/f"data/requests/{split}.json").read_bytes()).hexdigest(),
        "code_sha256":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/"src/route_agent").glob("*.py"))},
        "packages":{name:importlib.metadata.version(name) for name in ("networkx","pydantic","osmnx","streamlit")},
        "limited_search_feasible_retention":statistics.mean(recall) if recall else None,
        "mean_score_regret_vs_bounded_no_coarse":statistics.mean(regret) if regret else None,
        "limitations":["Template-generated synthetic scenarios; not an independent real-world benchmark.",
                       "Rule parser by default; no LLM accuracy claim.","Static snapshots; no actual trip-time ground truth.",
                       "No-verification variant is evaluation-only and never reports a verified route."]}
    (directory/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return directory,report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split",choices=["dev","test"],default="test")
    parser.add_argument("--repeat",type=int,default=1)
    parser.add_argument("--parser",choices=["rules","llm"],default="rules",help="llm explicitly enables paid model calls")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10:
        parser.error("repeat must be between 1 and 10")
    destination,report = evaluate(args.split,args.repeat,args.parser)
    print(destination)
    print(json.dumps(report,ensure_ascii=False,indent=2))
