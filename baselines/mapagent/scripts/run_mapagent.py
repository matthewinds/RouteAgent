"""Launch the released text/API pipeline with explicit, recorded settings."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime_config import model_id, validate_credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=["trip", "poi", "nearby", "routing", "unanswerable"], default="trip")
    parser.add_argument("--test-number", type=int, default=1, help="1 for smoke test; -1 for released split")
    parser.add_argument("--model", default=os.getenv("MAPAGENT_MODEL", "openai/gpt-3.5-turbo"))
    parser.add_argument("--check", action="store_true", help="Offline dependencies/data/config status; no API calls")
    args = parser.parse_args()
    if args.test_number == 0 or args.test_number < -1:
        parser.error("--test-number must be positive or -1")
    data = ROOT / "datasets_dir" / "txt_data" / args.task
    splits = json.loads((data / "pid_splits.json").read_text(encoding="utf-8"))
    problems = json.loads((data / "problems.json").read_text(encoding="utf-8"))
    ids = splits["minitest"]
    if args.test_number > 0:
        ids = ids[:args.test_number]
    for pid in ids:
        for field in ("question", "answer", "choices", "hint", "image", "split", "skill", "solution"):
            if field not in problems[pid]:
                raise RuntimeError(f"Missing field {field} in {args.task}/{pid}")
    packages = ["openai", "httpx", "googlemaps", "numpy", "func-timeout", "fuzzywuzzy", "beautifulsoup4", "python-dotenv", "tqdm"]
    versions = {name: importlib.metadata.version(name) for name in packages}
    model = model_id(args.model)
    map_model = model_id(os.getenv("MAPAGENT_MAP_MODEL") or model)
    print(f"Dataset: {args.task}; released split: minitest; selected: {len(ids)}", flush=True)
    print(f"LLM: {model}; map-tool LLM: {map_model}", flush=True)
    if args.check:
        print(json.dumps(versions, indent=2))
        print("Credentials: " + ", ".join(f"{key}={'set' if os.getenv(key) else 'missing'}"
              for key in ("OPENROUTER_API_KEY", "GOOGLE_MAP_API_KEY")))
        print("Offline check passed. Credentials, remote model and Maps access are not verified.")
        return 0
    validate_credentials()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output = ROOT / "outputs" / stamp
    output.mkdir(parents=True)
    command = [sys.executable, "-X", "utf8", str(ROOT / "src" / "run.py"),
               "--data_root", str(data), "--output_root", str(output), "--task_name", args.task,
               "--test_split", "minitest", "--test_number", str(args.test_number),
               "--label", "mapagent", "--seed", "0"]
    for stage in ("policy", "kr", "qg", "sg"):
        command.extend([f"--{stage}_engine", model])
    files = [ROOT / "utilities.py", ROOT / "parallel_function_implementation.py", ROOT / "runtime_config.py",
             ROOT / "src" / "run.py", ROOT / "src" / "model.py", data / "problems.json", data / "pid_splits.json",
             *sorted((ROOT / "src" / "demos").glob("*.py"))]
    manifest = {"created_utc": stamp, "python": sys.version, "packages": versions,
                "model": model, "map_model": map_model, "provider": "OpenRouter",
                "command": command, "pids": ids, "status": "started",
                "sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", MAPAGENT_MAP_MODEL=map_model)
    print(f"Run manifest: {manifest_path}", flush=True)
    log_path = output / "console.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT / "src", env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            for name in ("OPENROUTER_API_KEY", "GOOGLE_MAP_API_KEY", "OPENAI_API_KEY", "BING_API_KEY"):
                if os.getenv(name):
                    line = line.replace(os.environ[name], "[REDACTED]")
            log.write(line)
            log.flush()
        returncode = process.wait()
    if returncode == 0:
        try:
            result_path = output / args.task / "mapagent_minitest.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            cache_path = output / args.task / "mapagent_minitest_cache.jsonl"
            records = [json.loads(line) for line in cache_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if result["count"] != len(ids) or len(records) != len(ids):
                raise ValueError("Saved record count does not match selected examples")
            print(f"Completed: count={result['count']}, correct={result['correct']}, accuracy={result['acc']}%")
        except (OSError, ValueError, KeyError) as error:
            print(f"Output verification failed: {type(error).__name__}")
            returncode = 1
    print(f"Console log: {log_path}")
    manifest.update(status="completed" if returncode == 0 else "failed", returncode=returncode)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        sys.exit(str(error))
