import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "data-agent-rules" / "hooks"))
sys.path.insert(0, str(ROOT / "plugins" / "data-agent-rules" / "tools"))
sys.path.insert(0, str(ROOT / "evals"))

from cost_check import parse_bq_dry_run, parse_databricks_explain, parse_size, parse_snowflake_explain  # noqa: E402
from cost_guard import bq_queries  # noqa: E402
from scenarios import BqRevenue  # noqa: E402

HOOK = ROOT / "plugins" / "data-agent-rules" / "hooks" / "cost_guard.py"
TOOL = ROOT / "plugins" / "data-agent-rules" / "tools" / "cost_check.py"


def test_parse_size():
    assert parse_size("100GB") == 100 * 2 ** 30
    assert parse_size("1.5TiB") == int(1.5 * 2 ** 40)
    assert parse_size("2048") == 2048


def test_parse_bq_dry_run_json_and_text():
    assert parse_bq_dry_run('{"statistics": {"totalBytesProcessed": "12345"}}') == 12345
    assert parse_bq_dry_run("Query successfully validated. Assuming the tables are not modified, "
                            "running this query will process 987 bytes of data.") == 987


def test_parse_snowflake_explain():
    out = '[{"content": "{\\"GlobalStats\\":{\\"partitionsTotal\\":400,\\"partitionsAssigned\\":3,\\"bytesAssigned\\":1048576}}"}]'
    assert parse_snowflake_explain(out) == {"partitionsTotal": 400, "partitionsAssigned": 3, "bytesAssigned": 1048576}


def test_parse_databricks_explain():
    plan = ("== Optimized Logical Plan ==\nAggregate [country], Statistics(sizeInBytes=1.0 KiB)\n"
            "+- Relation main.prod.events[country,amount] parquet, Statistics(sizeInBytes=2.0 GiB)\n"
            "+- Relation main.prod.users[id] parquet, Statistics(sizeInBytes=512.0 MiB)")
    assert parse_databricks_explain(plan) == int(2.5 * 2 ** 30)


@pytest.mark.parametrize("command, expected", [
    ('bq query --use_legacy_sql=false "SELECT * FROM prod.events LIMIT 10"',
     [(["--use_legacy_sql=false"], "SELECT * FROM prod.events LIMIT 10")]),
    ("bq --location=US query --nouse_legacy_sql 'SELECT 1'", [(["--location=US", "--nouse_legacy_sql"], "SELECT 1")]),
    ('bq query --dry_run "SELECT 1" && bq ls prod', [(["--dry_run"], "SELECT 1")]),
    ('bq show --schema prod.events', []),
    ('echo "bq query is great"', []),
])
def test_bq_queries(command, expected):
    assert bq_queries(command) == expected


@pytest.fixture
def warehouse(tmp_path):
    con = duckdb.connect(str(tmp_path / "warehouse.duckdb"))
    BqRevenue().setup(con)
    con.close()
    (tmp_path / "bq.py").write_bytes((ROOT / "evals" / "bq.py").read_bytes())
    return tmp_path


def run(args, cwd, stdin=None, **env):
    return subprocess.run([sys.executable, *args], cwd=cwd, input=stdin, capture_output=True, text=True,
                          env={**os.environ, "DATA_AGENT_RULES_BQ": f"{sys.executable} bq.py", **env})


def test_limit_does_not_lower_the_bill(warehouse):
    full = run([str(TOOL), "SELECT * FROM prod.events LIMIT 10"], warehouse)
    assert full.returncode == 2 and "Over the" in full.stdout
    narrow = run([str(TOOL), "SELECT country, SUM(amount) FROM prod.events WHERE event_date = '2026-09-20' GROUP BY 1"],
                 warehouse)
    assert narrow.returncode == 0, narrow.stdout


def test_filter_on_non_partition_column_does_not_prune(warehouse):
    sql = "SELECT country FROM prod.events WHERE CAST(event_ts AS DATE) = '2026-09-20'"
    out = run([str(TOOL), "--max-scan", "10GB", sql], warehouse)
    assert out.returncode == 2, out.stdout


def hook(command, cwd, **env):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return run([str(HOOK)], cwd, stdin=payload, **env).stdout


def test_hook_asks_before_expensive_query(warehouse):
    out = hook('bq query --use_legacy_sql=false "SELECT * FROM prod.events LIMIT 10"', warehouse)
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "TiB" in decision["permissionDecisionReason"] and "$" in decision["permissionDecisionReason"]


def test_hook_passes_cheap_capped_and_dry_run_queries(warehouse):
    assert hook("bq query \"SELECT count(*) FROM prod.events WHERE event_date = '2026-09-20'\"", warehouse) == ""
    assert hook('bq query --maximum_bytes_billed=1000000 "SELECT * FROM prod.events"', warehouse) == ""
    assert hook('bq query --dry_run "SELECT * FROM prod.events"', warehouse) == ""
    assert hook('bq query "SELECT * FROM prod.events"', warehouse, DATA_AGENT_RULES_COST="off") == ""


def test_hook_silent_when_dry_run_fails(tmp_path):
    assert hook('bq query "SELECT * FROM nowhere"', tmp_path) == ""
