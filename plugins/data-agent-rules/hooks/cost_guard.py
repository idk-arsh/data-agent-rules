"""PreToolUse hook: price a BigQuery query before the agent runs it.

If a Bash command runs `bq query` without --dry_run or --maximum_bytes_billed,
run the same query as a free dry run first. If it would bill more than the
limit (DATA_AGENT_RULES_MAX_SCAN, default 100GB), return an "ask" decision with
the size and the dollar estimate. Under the limit, or if the dry run fails,
print nothing and the normal permission flow applies.

Set DATA_AGENT_RULES_COST=off to disable.
"""
import json
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from cost_check import (DEFAULT_MAX_SCAN, DEFAULT_PRICE_PER_TIB, TIB, bigquery_bytes,  # noqa: E402
                        human, parse_size)

SEPARATORS = {"&&", "||", ";", "|", "&"}
# bq query flags that take a separate value (`--flag value`); everything else is --flag or --flag=value.
VALUE_FLAGS = {"--parameter", "--destination_table", "--location", "--project_id", "--format", "--dataset_id",
               "--label", "--job_id", "--max_rows", "-n"}
PASSTHROUGH = ("--parameter", "--location", "--project_id", "--dataset_id", "--use_legacy_sql")


def bq_queries(command: str) -> list[tuple[list[str], str]]:
    """(flags, sql) for each `bq ... query ... "<SQL>"` in a shell command."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return []
    found, i = [], 0
    while i < len(tokens):
        if Path(tokens[i]).name.lower() in ("bq", "bq.cmd") and "query" in tokens[i + 1:i + 6]:
            flags, positional, j = [], [], i + 1
            while j < len(tokens) and tokens[j] not in SEPARATORS:
                t = tokens[j]
                if t.startswith("-"):
                    if t in VALUE_FLAGS and j + 1 < len(tokens):
                        flags.append(f"{t}={tokens[j + 1]}")
                        j += 1
                    else:
                        flags.append(t)
                elif t != "query":
                    positional.append(t)
                j += 1
            if positional:
                found.append((flags, positional[-1]))
            i = j
        i += 1
    return found


def main() -> None:
    if os.environ.get("DATA_AGENT_RULES_COST", "").lower() == "off":
        return
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command", "")
    limit = parse_size(os.environ.get("DATA_AGENT_RULES_MAX_SCAN", DEFAULT_MAX_SCAN))
    price = float(os.environ.get("DATA_AGENT_RULES_TIB_PRICE", DEFAULT_PRICE_PER_TIB))
    over = []
    for flags, sql in bq_queries(command):
        names = {f.split("=", 1)[0] for f in flags}
        if names & {"--dry_run", "--maximum_bytes_billed"}:
            continue
        try:
            n = bigquery_bytes(sql, [f for f in flags if f.startswith(PASSTHROUGH)], timeout=25)
        except Exception:  # noqa: BLE001 - no estimate, let the query's own error show
            continue
        if n > limit:
            over.append(f"{human(n)} (~${n / TIB * price:,.2f})")
    if not over:
        return
    reason = ("data-agent-rules: a dry run says this BigQuery query will bill " + ", ".join(over) +
              f", over the {human(limit)} limit. Rule 7: select only the columns you need, filter on the "
              "partition column, and preview with `bq head` (LIMIT does not reduce bytes billed). "
              f"Or add --maximum_bytes_billed={limit}. Confirm to run it anyway.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
