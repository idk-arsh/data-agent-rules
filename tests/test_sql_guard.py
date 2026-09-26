"""Tests for the destructive-SQL seatbelt hook.  Run: python -m pytest tests"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "plugins" / "data-agent-rules" / "hooks" / "sql_guard.py"
sys.path.insert(0, str(GUARD.parent))
from sql_guard import findings  # noqa: E402

ASK = [
    'python run_sql.py "DROP TABLE prod.orders"',
    'duckdb w.db "drop schema staging cascade"',
    'psql -c "TRUNCATE TABLE events"',
    'python run_sql.py "CREATE OR REPLACE TABLE prod.orders AS SELECT DISTINCT * FROM prod.orders"',
    'spark-sql -e "INSERT OVERWRITE TABLE sales SELECT * FROM tmp"',
    "python -c \"df.write.mode('overwrite').saveAsTable('gold.sales')\"",
    "dbt run --select orders --full-refresh",
    'python run_sql.py "DELETE FROM prod.customers"',
    'python run_sql.py "UPDATE prod.products SET price = price / 10"',
    'python run_sql.py "ALTER TABLE prod.orders DROP COLUMN amount"',
    'python run_sql.py "SELECT 1; DELETE FROM orders"',
]

ALLOW = [
    'python run_sql.py "SELECT COUNT(*) FROM prod.orders"',
    'python run_sql.py "DELETE FROM dev.tmp WHERE id = 3"',
    'python run_sql.py "UPDATE prod.products SET price = price / 10 WHERE load_id = 42"',
    'python run_sql.py "CREATE TABLE dev.orders_dedup AS SELECT DISTINCT * FROM prod.orders"',
    "dbt build --select state:modified+",
    "ls -la",
]


@pytest.mark.parametrize("command", ASK)
def test_flags_destructive(command):
    assert findings(command)


@pytest.mark.parametrize("command", ALLOW)
def test_allows_safe(command):
    assert findings(command) == []


def run_hook(command, env_extra=None):
    import os
    env = {**os.environ, **(env_extra or {})}
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True, env=env)


def test_hook_asks():
    out = json.loads(run_hook('python run_sql.py "DROP TABLE prod.orders"').stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_hook_silent_on_safe():
    assert run_hook("ls").stdout == ""


def test_hook_can_be_disabled():
    assert run_hook("dbt run --full-refresh", {"DATA_AGENT_RULES_GUARD": "off"}).stdout == ""
