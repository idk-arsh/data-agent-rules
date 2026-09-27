"""cost-check: price a warehouse query before it runs.

  python cost_check.py "SELECT ..."                        # BigQuery dry run (free), bytes and dollars
  python cost_check.py --engine snowflake "SELECT ..."     # EXPLAIN: bytes and partitions it will scan
  python cost_check.py --engine databricks "SELECT ..."    # EXPLAIN COST: Spark's size estimate
  python cost_check.py --max-scan 50GB "SELECT ..."

BigQuery runs `bq query --dry_run`, which is free and returns the exact bytes the
query would bill. Snowflake runs `snow sql -q "EXPLAIN USING JSON ..."`, which
reports the bytes and micro-partitions assigned after pruning; Snowflake bills
warehouse time, not bytes, so there is no dollar figure. Databricks runs
EXPLAIN COST through databricks-sql-connector (DATABRICKS_SERVER_HOSTNAME,
DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN); Spark's sizes are estimates.

Exit code 0 when the scan is under --max-scan (default 100GB, or
DATA_AGENT_RULES_MAX_SCAN), 2 when over, 1 when the estimate failed.
DATA_AGENT_RULES_BQ overrides the bq command (e.g. "python bq.py").
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys

TIB = 2 ** 40
UNITS = {"B": 1, "KB": 2 ** 10, "MB": 2 ** 20, "GB": 2 ** 30, "TB": 2 ** 40, "PB": 2 ** 50,
         "KIB": 2 ** 10, "MIB": 2 ** 20, "GIB": 2 ** 30, "TIB": 2 ** 40, "PIB": 2 ** 50}
DEFAULT_MAX_SCAN = "100GB"
DEFAULT_PRICE_PER_TIB = 6.25  # BigQuery on-demand, US multi-region


def parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGTP]?I?B)?\s*", text.upper())
    if not m:
        raise ValueError(f"can't read size {text!r}; use e.g. 100GB or 1.5TiB")
    return int(float(m.group(1)) * UNITS[m.group(2) or "B"])


def human(n: float) -> str:
    for unit in ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]:
        if n < 1024 or unit == "PiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


def bq_command() -> list[str]:
    return shlex.split(os.environ.get("DATA_AGENT_RULES_BQ", "bq"), posix=os.name != "nt")


def bigquery_bytes(sql: str, extra_flags: list[str] | None = None, timeout: int = 60) -> int:
    """Bytes a BigQuery query would bill, from a free dry run."""
    cmd = bq_command() + ["--format=json", "query", "--dry_run", "--use_legacy_sql=false",
                          *(extra_flags or []), sql]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return parse_bq_dry_run(proc.stdout + proc.stderr)


def parse_bq_dry_run(out: str) -> int:
    m = re.search(r'"totalBytesProcessed"\s*:\s*"?(\d+)', out) or re.search(r"will process (?:upper bound of )?(\d+) bytes", out)
    if not m:
        raise RuntimeError("bq dry run failed: " + out.strip()[:400])
    return int(m.group(1))


def snowflake_stats(sql: str, timeout: int = 120) -> dict:
    proc = subprocess.run(["snow", "sql", "--format", "json", "-q", "EXPLAIN USING JSON " + sql],
                          capture_output=True, text=True, timeout=timeout)
    return parse_snowflake_explain(proc.stdout + proc.stderr)


def parse_snowflake_explain(out: str) -> dict:
    stats = {}
    for key in ("partitionsTotal", "partitionsAssigned", "bytesAssigned"):
        m = re.search(rf'\\*"{key}\\*"\s*:\s*(\d+)', out)
        if m:
            stats[key] = int(m.group(1))
    if "bytesAssigned" not in stats:
        raise RuntimeError("snowflake EXPLAIN failed: " + out.strip()[:400])
    return stats


def databricks_plan(sql: str) -> str:
    from databricks import sql as dbsql  # pip install databricks-sql-connector
    with dbsql.connect(server_hostname=os.environ["DATABRICKS_SERVER_HOSTNAME"],
                       http_path=os.environ["DATABRICKS_HTTP_PATH"],
                       access_token=os.environ["DATABRICKS_TOKEN"]) as conn, conn.cursor() as cur:
        cur.execute("EXPLAIN COST " + sql)
        return "\n".join(str(r[0]) for r in cur.fetchall())


def parse_databricks_explain(plan: str) -> int:
    """Sum of sizeInBytes over the leaf relations (the tables being read)."""
    sizes = [parse_size(f"{num}{unit}") for num, unit in
             re.findall(r"(?:Relation|Scan)[^\n]*sizeInBytes=([\d.]+)\s*([KMGTP]?i?B)", plan)]
    if not sizes:
        raise RuntimeError("no table sizes in EXPLAIN COST output")
    return sum(sizes)


ADVICE = ("To scan less: select only the columns you need (SELECT * reads every column), filter on the "
          "partition or cluster column, and preview rows with `bq head` or TABLESAMPLE; "
          "LIMIT does not reduce bytes billed on BigQuery.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Price a warehouse query before it runs.")
    ap.add_argument("sql")
    ap.add_argument("--engine", choices=["bigquery", "snowflake", "databricks"], default="bigquery")
    ap.add_argument("--max-scan", default=os.environ.get("DATA_AGENT_RULES_MAX_SCAN", DEFAULT_MAX_SCAN))
    ap.add_argument("--price-per-tib", type=float,
                    default=float(os.environ.get("DATA_AGENT_RULES_TIB_PRICE", DEFAULT_PRICE_PER_TIB)))
    args = ap.parse_args()
    limit = parse_size(args.max_scan)
    try:
        if args.engine == "bigquery":
            n = bigquery_bytes(args.sql)
            line = f"BigQuery will bill {human(n)} (~${n / TIB * args.price_per_tib:,.2f} at ${args.price_per_tib}/TiB on-demand)."
        elif args.engine == "snowflake":
            s = snowflake_stats(args.sql)
            n = s["bytesAssigned"]
            line = (f"Snowflake will scan {human(n)}, {s.get('partitionsAssigned', '?')} of "
                    f"{s.get('partitionsTotal', '?')} micro-partitions.")
        else:
            n = parse_databricks_explain(databricks_plan(args.sql))
            line = f"Spark estimates the tables read at {human(n)} (an estimate, before runtime pruning)."
    except Exception as e:  # noqa: BLE001 - any failure means no estimate
        print(f"cost-check: no estimate ({e})")
        return 1
    if n > limit:
        print(f"{line} Over the {human(limit)} limit. {ADVICE}")
        return 2
    print(f"{line} Under the {human(limit)} limit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
