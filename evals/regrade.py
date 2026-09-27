"""Regrade a finished run from its sandboxes (after a grader fix): python regrade.py results/<dir>"""
import json
import sys
import tempfile
from pathlib import Path

from scenarios import ALL

out = Path(sys.argv[1])
by_name = {s.name: s for s in ALL}
sandbox_root = Path(tempfile.gettempdir()) / "data-agent-rules-evals"
records = [json.loads(line) for line in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
root = sandbox_root / out.name.replace("-claude-", "-", 1)  # results/<stamp>-claude-<model> -> <stamp>-<model>
for r in records:
    run_dir = root / r["sandbox"]
    r["safe"], r["useful"], r["notes"] = by_name[r["scenario"]].grade(run_dir, r["final_text"])
    print(f"{r['scenario']:12} {r['arm']:11} run {r['run']} safe={r['safe']!s:5} useful={r['useful']!s:5} {r['notes']}")
(out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
