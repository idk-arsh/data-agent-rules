"""PreToolUse hook: ask before a shell command selects personal-looking columns raw.

If a Bash command runs a SELECT that names a personal-looking column or table
(email, phone, ssn, address, customers, users, ...) and doesn't go through
safe_peek, return an "ask" decision that points the agent at safe_peek.
Aggregate-only queries (COUNT, SUM, AVG, COUNT(DISTINCT ...)) pass.

Set DATA_AGENT_RULES_PII=off to disable.
"""
import json
import os
import re
import sys
from pathlib import Path

PII_WORD = re.compile(
    r"\b(\w*_)?(e?mail|email_address|phone|mobile|ssn|address|street|dob|birth_?date|date_of_birth|"
    r"ip_address|card_number|iban|passport|customers?|users?|contacts?|patients?|employees?|people|persons?)\b",
    re.IGNORECASE)
SELECT = re.compile(r"\bselect\b(.*?)\bfrom\b", re.IGNORECASE | re.DOTALL)
AGGREGATE = re.compile(r"^\s*(count|sum|avg|approx_count_distinct|stddev\w*|variance)\s*\(", re.IGNORECASE)
SAFE_PEEK = Path(__file__).resolve().parent.parent / "tools" / "safe_peek.py"


def split_top(select_list: str) -> list[str]:
    items, depth, cur = [], 0, ""
    for ch in select_list:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    return items + [cur]


def aggregate_only(statement: str) -> bool:
    m = SELECT.search(statement)
    return bool(m) and all(AGGREGATE.match(item) for item in split_top(m.group(1)) if item.strip())


def needs_peek(command: str) -> bool:
    if "safe_peek" in command or not re.search(r"\bselect\b", command, re.IGNORECASE):
        return False
    for statement in command.split(";"):
        if re.search(r"\bselect\b", statement, re.IGNORECASE) and PII_WORD.search(statement) \
                and not aggregate_only(statement):
            return True
    return False


def main() -> None:
    if os.environ.get("DATA_AGENT_RULES_PII", "").lower() == "off":
        return
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command", "")
    if not needs_peek(command):
        return
    reason = ("data-agent-rules: this query reads personal-looking columns raw. Rule 6: preview them with "
              f'python "{SAFE_PEEK}" "<SELECT ...>" (masks personal data, keeps its shape), '
              "or use aggregates. Confirm to run it raw anyway.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
