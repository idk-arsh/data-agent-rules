"""Tests for safe_peek and the PII hook.  Run: python -m pytest tests"""
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "data-agent-rules"
sys.path.insert(0, str(PLUGIN / "tools"))
sys.path.insert(0, str(PLUGIN / "hooks"))
from pii_guard import needs_peek  # noqa: E402
from safe_peek import looks_personal, mask, shape  # noqa: E402

PEEK = PLUGIN / "tools" / "safe_peek.py"
GUARD = PLUGIN / "hooks" / "pii_guard.py"


@pytest.mark.parametrize("raw, masked", [
    ("user42@example.com", "u*****@example.com"),
    ("user2@@example.com", "u****@@example.com"),
    ("user 3@example.com", "u*** #@example.com"),
    ("555-0100", "###-####"),
    ("Ada Lovelace", "A** L*******"),
])
def test_mask_keeps_shape(raw, masked):
    assert mask(raw) == masked


def test_shape():
    assert shape("user1@ex.com") == "aaaa9@aa.aaa"


@pytest.mark.parametrize("value", ["a@b.co", "123-45-6789", "10.0.0.1", "4111 1111 1111 1111", "+1 (555) 010-1234"])
def test_detects_personal(value):
    assert looks_personal(value)


@pytest.mark.parametrize("value", ["north", "Product 12", "42", "3.14"])
def test_ignores_non_personal(value):
    assert not looks_personal(value)


@pytest.fixture
def warehouse(tmp_path):
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT i AS id, 'user' || i || '@example.com' AS contact, "
                "'north' AS region FROM range(1, 51) r(i)")
    con.close()
    return db


def peek(db, *args):
    return subprocess.run([sys.executable, str(PEEK), "--db", str(db), *args], capture_output=True, text=True)


def test_cli_masks_by_value_and_limits(warehouse):
    out = peek(warehouse, "SELECT * FROM t").stdout
    assert "user1@example.com" not in out and "u****@example.com" in out
    assert "north" in out
    assert "20 rows shown" in out


def test_cli_profile_has_no_raw_values(warehouse):
    out = peek(warehouse, "--profile", "SELECT contact FROM t").stdout
    assert "@example.com" not in out.replace("aaaaaaa.aaa", "")
    assert "aaaa99@aaaaaaa.aaa" in out


def test_cli_refuses_writes(warehouse):
    r = peek(warehouse, "DELETE FROM t")
    assert r.returncode == 2
    r = peek(warehouse, "SELECT 1; DROP TABLE t")
    assert r.returncode == 2


ASK = [
    'python run_sql.py "SELECT email FROM prod.customers WHERE email NOT LIKE \'%@%\'"',
    'python run_sql.py "SELECT * FROM prod.customers LIMIT 5"',
    'psql -c "select id, phone from contacts"',
    'python run_sql.py "SELECT MIN(email) FROM prod.customers"',
]
ALLOW = [
    'python run_sql.py "SELECT COUNT(*) FROM prod.customers"',
    'python run_sql.py "SELECT COUNT(*) FILTER (WHERE email NOT LIKE \'%@%\') AS bad, COUNT(DISTINCT email) FROM prod.customers"',
    'python safe_peek.py "SELECT email FROM prod.customers"',
    'python run_sql.py "SELECT region, avg(amount) FROM prod.events GROUP BY region"',
    "ls -la",
]


@pytest.mark.parametrize("command", ASK)
def test_hook_flags_raw_pii(command):
    assert needs_peek(command)


@pytest.mark.parametrize("command", ALLOW)
def test_hook_allows(command):
    assert not needs_peek(command)


def run_hook(command, env_extra=None):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True,
                          env={**os.environ, **(env_extra or {})})


def test_hook_output_and_switch():
    out = json.loads(run_hook(ASK[0]).stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "safe_peek" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert run_hook(ASK[0], {"DATA_AGENT_RULES_PII": "off"}).stdout == ""
