"""End-to-end check of the MCP server over stdio, as an MCP client would run it.

  python tests/mcp_smoke.py [server command...]    (default: python mcp_server/data_agent_rules_mcp/server.py)

Builds a DuckDB file with personal data and the stand-in bq warehouse, starts the
server, lists its tools and prompt, and calls safe_peek and cost_check.
"""
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

import duckdb
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
from scenarios import BqRevenue  # noqa: E402


def text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


async def main() -> int:
    work = Path(tempfile.mkdtemp())
    con = duckdb.connect(str(work / "warehouse.duckdb"))
    BqRevenue().setup(con)
    con.execute("CREATE TABLE prod.customers AS SELECT 1 AS id, 'jane.doe@example.com' AS email, '+15551234567' AS phone")
    con.close()
    shutil.copy(ROOT / "evals" / "bq.py", work / "bq.py")
    cmd = sys.argv[1:] or [sys.executable, str(ROOT / "mcp_server" / "data_agent_rules_mcp" / "server.py")]
    params = StdioServerParameters(command=cmd[0], args=cmd[1:], cwd=str(work), env={
        **os.environ, "DATA_AGENT_RULES_DB": str(work / "warehouse.duckdb"),
        "DATA_AGENT_RULES_BQ": f'"{sys.executable}" bq.py'})
    failures = []

    def check(name, ok, detail):
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"\n  {detail}"))
        if not ok:
            failures.append(name)

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        check("lists safe_peek and cost_check", {"safe_peek", "cost_check"} <= tools, tools)
        prompts = {p.name for p in (await session.list_prompts()).prompts}
        check("lists data-agent-rules prompt", "data-agent-rules" in prompts, prompts)

        out = text(await session.call_tool("safe_peek", {"sql": "SELECT * FROM prod.customers"}))
        check("safe_peek masks email and phone", "jane.doe" not in out and "5551234567" not in out and "@example.com" in out, out)
        out = text(await session.call_tool("safe_peek", {"sql": "DELETE FROM prod.customers"}))
        check("safe_peek refuses writes", "read-only" in out, out)
        out = text(await session.call_tool("cost_check", {"sql": "SELECT * FROM prod.events LIMIT 5"}))
        check("cost_check flags SELECT * LIMIT", "Over the" in out and "$193" in out, out)
        out = text(await session.call_tool("cost_check", {
            "sql": "SELECT country, SUM(amount) FROM prod.events WHERE event_date = '2026-09-20' GROUP BY 1"}))
        check("cost_check passes a pruned query", "Under the" in out, out)
        rules = await session.get_prompt("data-agent-rules")
        check("prompt returns the rules", "Look before you scan" in str(rules), str(rules)[:200])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
