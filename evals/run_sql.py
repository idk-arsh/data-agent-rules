"""SQL runner the agent uses inside an eval sandbox.

Usage: python run_sql.py "SELECT ..."
Runs the SQL against warehouse.duckdb in the current directory, logs every
statement to queries.log (one JSON object per line), and prints at most 50 rows.
"""
import json
import sys
import time

import duckdb

MAX_ROWS = 50


def main() -> int:
    sql = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    with open("queries.log", "a", encoding="utf-8") as log:
        log.write(json.dumps({"t": time.time(), "sql": sql}) + "\n")
    con = duckdb.connect("warehouse.duckdb")
    try:
        result = con.execute(sql)
        if result.description:
            print(" | ".join(d[0] for d in result.description))
            rows = result.fetchmany(MAX_ROWS + 1)
            for row in rows[:MAX_ROWS]:
                print(" | ".join(str(v) for v in row))
            if len(rows) > MAX_ROWS:
                print(f"... output truncated at {MAX_ROWS} rows")
        else:
            print("OK")
        return 0
    except duckdb.Error as e:
        print(f"ERROR: {e}")
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
