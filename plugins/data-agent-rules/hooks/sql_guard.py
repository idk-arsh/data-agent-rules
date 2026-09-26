"""PreToolUse seatbelt: ask the user before a shell command runs destructive SQL.

Reads the hook payload on stdin. If the Bash command contains a destructive
statement, prints a PreToolUse "ask" decision so the user confirms it. Otherwise
prints nothing and the normal permission flow applies.

Set DATA_AGENT_RULES_GUARD=off to disable.
"""
import json
import os
import re
import sys

FLAGS = re.IGNORECASE | re.DOTALL

# Patterns that are destructive wherever they appear.
ALWAYS = [
    (r"\bDROP\s+(TABLE|SCHEMA|DATABASE|VIEW|CATALOG)\b", "DROP"),
    (r"\bALTER\s+TABLE\b[^;]*\bDROP\s+(COLUMN|PARTITION)\b", "ALTER TABLE ... DROP"),
    (r"\bTRUNCATE\s+(TABLE\s+)?[\w`\"\[]", "TRUNCATE"),
    (r"\bCREATE\s+OR\s+REPLACE\s+TABLE\b", "CREATE OR REPLACE TABLE"),
    (r"\bINSERT\s+OVERWRITE\b", "INSERT OVERWRITE"),
    (r"\.mode\(\s*\\?[\"']overwrite\\?[\"']\s*\)", 'mode("overwrite")'),
    (r"\bdbt\b[^\n;|&]*--full-refresh\b", "dbt --full-refresh"),
]

# Patterns that are destructive only when the statement has no WHERE clause.
NEEDS_WHERE = [
    (r"\bDELETE\s+FROM\b", "DELETE without WHERE"),
    (r"\bUPDATE\s+[\w.`\"\[\]]+\s+SET\b", "UPDATE without WHERE"),
]


def findings(command: str) -> list[str]:
    found = [label for pattern, label in ALWAYS if re.search(pattern, command, FLAGS)]
    for statement in command.split(";"):
        for pattern, label in NEEDS_WHERE:
            if re.search(pattern, statement, FLAGS) and not re.search(r"\bWHERE\b", statement, FLAGS):
                found.append(label)
    return sorted(set(found))


def main() -> None:
    if os.environ.get("DATA_AGENT_RULES_GUARD", "").lower() == "off":
        return
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command", "")
    found = findings(command)
    if not found:
        return
    reason = (
        "data-agent-rules: this command contains " + ", ".join(found) + ". "
        "Rule 4: show the affected row count and the undo path, then confirm before running."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))


if __name__ == "__main__":
    main()
