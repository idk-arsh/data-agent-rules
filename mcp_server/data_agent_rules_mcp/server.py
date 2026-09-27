"""MCP server: safe_peek and cost_check for any MCP client (Cursor, Claude Desktop, VS Code, Windsurf, ...).

Tools
  safe_peek   one read-only query, row-limited, personal data masked (keeps its shape)
  cost_check  price a BigQuery / Snowflake / Databricks query before running it
Prompt
  data-agent-rules   the 8 rules, for clients that don't read AGENTS.md

Default connection for safe_peek: DATA_AGENT_RULES_URL (any SQLAlchemy URL) or
DATA_AGENT_RULES_DB (a DuckDB file), set in the client's MCP config. A call can
pass db or url to override.
"""
import os
import sys
from pathlib import Path

try:  # installed package: the tools are copied next to this file
    from . import cost_check as _cost, safe_peek as _peek  # type: ignore[attr-defined]
    RULES = Path(__file__).with_name("AGENTS.md")
except ImportError:  # running from a checkout
    ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(ROOT / "plugins" / "data-agent-rules" / "tools"))
    import cost_check as _cost  # noqa: E402
    import safe_peek as _peek  # noqa: E402
    RULES = ROOT / "AGENTS.md"

try:
    from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x

INSTRUCTIONS = (
    "Data safety tools. Use safe_peek instead of a raw SELECT whenever you look at rows: it masks emails, phones, "
    "names, SSNs, IPs and card numbers but keeps their shape, so you can still see what's wrong. Use profile=true "
    "to see value patterns and counts with no raw values. Before any BigQuery, Snowflake or Databricks query that "
    "isn't a small lookup, call cost_check; on BigQuery, LIMIT does not reduce the bytes billed.")

server = Server("data-agent-rules", instructions=INSTRUCTIONS)


@server.tool()
def safe_peek(sql: str, db: str | None = None, url: str | None = None, limit: int = 20,
              profile: bool = False) -> str:
    """Run one read-only query (SELECT/WITH/DESCRIBE) and return the rows with personal data masked.

    sql: the query. db: a DuckDB file. url: a SQLAlchemy URL (Postgres, Snowflake, Databricks, ...).
    limit: rows to return (default 20). profile: return per-column null/distinct counts and the most
    common value shapes instead of rows (no raw values at all).
    """
    url = url or (None if db else os.environ.get("DATA_AGENT_RULES_URL"))
    db = db or os.environ.get("DATA_AGENT_RULES_DB")
    _, out = _peek.peek(sql, db, url, min(max(limit, 1), 200), profile)
    return out


@server.tool()
def cost_check(sql: str, engine: str = "bigquery", max_scan: str | None = None) -> str:
    """Price a warehouse query before running it. Does not run the query.

    engine: bigquery (free dry run: exact bytes billed and dollars), snowflake (EXPLAIN: bytes and
    micro-partitions after pruning), or databricks (EXPLAIN COST: Spark's size estimate).
    max_scan: the limit to compare against, e.g. "100GB" (default 100GB or DATA_AGENT_RULES_MAX_SCAN).
    """
    _, message = _cost.estimate(sql, engine, max_scan)
    return message


@server.prompt(name="data-agent-rules")
def data_agent_rules() -> str:
    """The 8 rules for agents working on data: look before you scan, never invent values, writes go to dev, ..."""
    return RULES.read_text(encoding="utf-8")


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
