"""Read-only inventory of released dataset splits and visual assets."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit():
    rows = []
    for path in sorted((ROOT / "datasets_dir").rglob("problems.json")):
        problems = json.loads(path.read_text(encoding="utf-8"))
        split_path = path.with_name("pid_splits.json")
        splits = json.loads(split_path.read_text(encoding="utf-8")) if split_path.exists() else {}
        ids = splits.get("test", [])
        missing = [pid for pid in ids if pid not in problems]
        rows.append({"dataset": str(path.parent.relative_to(ROOT)), "problems": len(problems),
                     "splits": {k: len(v) for k, v in splits.items()}, "missing_test_ids": missing})
    return rows


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
