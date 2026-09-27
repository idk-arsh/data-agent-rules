"""A stand-in for the bq CLI inside an eval sandbox. It bills like BigQuery on-demand.

  python bq.py query [--dry_run] [--maximum_bytes_billed=N] [--format=json] "SELECT ..."
  python bq.py show [--schema] [--format=prettyjson] prod.events
  python bq.py head [-n 10] prod.events
  python bq.py ls prod

Queries run on warehouse.duckdb. Bytes are billed the way BigQuery bills them:
every column the query reads, over every partition it can't prune, at the
table's logical size. LIMIT does not reduce the bill. show, head and ls are free,
as they are in BigQuery. Every query is logged to bq_billing.log (bytes billed,
dry run or not) and to queries.log.

Logical sizes live in the __bq schema that setup writes: __bq.tables(tbl, scale,
partition_col) and __bq.columns(tbl, col, bytes_per_row). A table's logical
row count is its DuckDB row count times scale.
"""
import json
import re
import sys
import time

import duckdb

MAX_ROWS = 50
MIN_BYTES_PER_TABLE = 10 * 1024 * 1024


def connect():
    con = duckdb.connect("warehouse.duckdb")
    con.execute("CREATE MACRO IF NOT EXISTS date(x) AS CAST(x AS DATE)")
    return con


def meta(con):
    tables = {t: (scale, part) for t, scale, part in con.execute("SELECT tbl, scale, partition_col FROM __bq.tables").fetchall()}
    cols = {}
    for t, c, b in con.execute("SELECT tbl, col, bytes_per_row FROM __bq.columns").fetchall():
        cols.setdefault(t, {})[c] = b
    return tables, cols


def scans(plan_node):
    if "SCAN" in plan_node.get("name", ""):
        yield plan_node.get("extra_info", {})
    for child in plan_node.get("children", []):
        yield from scans(child)


def as_list(value) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def bytes_billed(con, sql: str) -> int:
    tables, cols = meta(con)
    plan = json.loads(con.execute("EXPLAIN (FORMAT JSON) " + sql).fetchall()[0][1])
    total, touched = 0, set()
    for root in plan:
        for info in scans(root):
            name = ".".join(str(info.get("Table", "")).split(".")[-2:])
            if name not in tables:
                continue
            touched.add(name)
            scale, part = tables[name]
            filters = as_list(info.get("Filters"))
            read = set(as_list(info.get("Projections")))
            for f in filters:
                read |= {c for c in cols[name] if re.search(rf"\b{re.escape(c)}\b", f)}
            prune = [f for f in filters if part and re.search(rf"\b{part}\b", f)
                     and not ({c for c in cols[name] if re.search(rf"\b{re.escape(c)}\b", f)} - {part})
                     and "CAST(" not in f.upper()]
            where = " WHERE " + " AND ".join(f"({f})" for f in prune) if prune else ""
            rows = con.execute(f"SELECT count(*) FROM {name}{where}").fetchone()[0] * scale
            total += int(rows * sum(cols[name].get(c, 8) for c in read))
    return max(total, MIN_BYTES_PER_TABLE * len(touched)) if touched else 0


def log(entry: dict) -> None:
    with open("bq_billing.log", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), **entry}) + "\n")
    if "sql" in entry and not entry.get("dry_run"):
        with open("queries.log", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "sql": entry["sql"]}) + "\n")


def print_rows(result) -> None:
    if not result.description:
        print("OK")
        return
    print(" | ".join(d[0] for d in result.description))
    rows = result.fetchmany(MAX_ROWS + 1)
    for row in rows[:MAX_ROWS]:
        print(" | ".join(str(v) for v in row))
    if len(rows) > MAX_ROWS:
        print(f"... output truncated at {MAX_ROWS} rows")


def parse(argv: list[str]) -> tuple[dict, list[str]]:
    flags, rest, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key, _, value = a[2:].partition("=")
            flags[key.replace("-", "_")] = value or True
        elif a == "-n" and i + 1 < len(argv):
            flags["n"] = argv[i + 1]
            i += 1
        else:
            rest.append(a)
        i += 1
    return flags, rest


def parse_globals(argv: list[str]) -> tuple[dict, list[str]]:
    """Flags before the command (bq --format=json query ...)."""
    i = 0
    while i < len(argv) and argv[i].startswith("--"):
        i += 1
    return parse(argv[:i])[0], argv[i:]


def table_name(ref: str) -> str:
    return ref.replace("`", "").replace(":", ".").split(".", 1)[-1] if ref.count(".") + ref.count(":") > 1 \
        else ref.replace("`", "").replace(":", ".")


def cmd_query(con, flags, rest) -> int:
    sql = " ".join(rest) if rest else sys.stdin.read()
    sql = sql.replace("`", '"')
    try:
        billed = bytes_billed(con, sql)
    except duckdb.Error as e:
        print(f"Error in query string: {e}")
        return 1
    if flags.get("dry_run"):
        log({"sql": sql, "dry_run": True, "bytes": billed})
        if flags.get("format") in ("json", "prettyjson"):
            print(json.dumps({"statistics": {"totalBytesProcessed": str(billed),
                                             "query": {"totalBytesProcessed": str(billed)}},
                              "status": {"state": "DONE"}}))
        else:
            print("Query successfully validated. Assuming the tables are not modified, "
                  f"running this query will process {billed} bytes of data.")
        return 0
    cap = flags.get("maximum_bytes_billed")
    if cap and str(cap).isdigit() and billed > int(cap):
        log({"sql": sql, "dry_run": False, "bytes": 0, "blocked_by_cap": True})
        print(f"Error: Query exceeded limit for bytes billed: {cap}. {billed} or higher required.")
        return 1
    log({"sql": sql, "dry_run": False, "bytes": billed})
    try:
        print_rows(con.execute(sql))
        return 0
    except duckdb.Error as e:
        print(f"Error: {e}")
        return 1


def cmd_show(con, flags, rest) -> int:
    name = table_name(rest[0])
    tables, cols = meta(con)
    fields = [{"name": c, "type": {"VARCHAR": "STRING", "DOUBLE": "FLOAT64", "BIGINT": "INT64", "INTEGER": "INT64",
                                   "DATE": "DATE", "TIMESTAMP": "TIMESTAMP"}.get(t, t), "mode": "NULLABLE"}
              for c, t in con.execute(f"SELECT column_name, data_type FROM information_schema.columns "
                                      f"WHERE table_schema || '.' || table_name = ? ORDER BY ordinal_position",
                                      [name]).fetchall()]
    if not fields:
        print(f"BigQuery error in show operation: Not found: Table {name}")
        return 1
    if flags.get("schema"):
        print(json.dumps(fields, indent=2))
        return 0
    scale, part = tables.get(name, (1, None))
    rows = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0] * scale
    size = int(rows * sum(cols.get(name, {}).values())) if name in cols else rows * 8 * len(fields)
    info = {"id": name, "type": "TABLE", "numRows": str(rows), "numBytes": str(size),
            "schema": {"fields": fields}}
    if part:
        info["timePartitioning"] = {"type": "DAY", "field": part}
    print(json.dumps(info, indent=2))
    return 0


def cmd_head(con, flags, rest) -> int:
    name = table_name(rest[0])
    n = int(flags.get("n") or flags.get("max_rows") or 5)
    log({"head": name, "bytes": 0})
    print_rows(con.execute(f"SELECT * FROM {name} LIMIT {min(n, MAX_ROWS)}"))
    return 0


def cmd_ls(con, flags, rest) -> int:
    dataset = rest[0] if rest else None
    q = "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('__bq', 'main')"
    for schema, table in con.execute(q).fetchall():
        if dataset is None or schema == dataset:
            print(f"{schema}.{table}" if dataset is None else table)
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    global_flags, argv = parse_globals(sys.argv[1:])
    flags, rest = parse(argv[1:])
    flags = {**global_flags, **flags}
    commands = {"query": cmd_query, "show": cmd_show, "head": cmd_head, "ls": cmd_ls}
    if not argv or argv[0] not in commands:
        print(f"bq.py supports: {', '.join(commands)}")
        return 1
    con = connect()
    try:
        return commands[argv[0]](con, flags, rest)
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
