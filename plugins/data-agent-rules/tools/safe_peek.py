"""safe-peek: look at data without putting raw personal data into an agent's context.

  python safe_peek.py "SELECT * FROM prod.customers WHERE email NOT LIKE '%@%'"
  python safe_peek.py --profile "SELECT email, phone FROM prod.customers"
  python safe_peek.py --db other.duckdb --limit 10 "SELECT ..."
  python safe_peek.py --url postgresql://... "SELECT ..."        # needs sqlalchemy

Runs one read-only query with a row limit. Columns that look personal (by name,
or because their values look like emails, phones, SSNs, IPs or card numbers) are
masked before printing. Masking keeps the shape of the value: separators, '@',
spaces and length stay visible, so "user 3@@x.com" prints as "u*** #@@x.com"
and you can still see what is wrong with it.

--profile prints per-column null and distinct counts and the most common value
shapes (letters -> a, digits -> 9) instead of rows. No raw values at all.

This is a seatbelt, not a DLP system: detection is by column name and regex, and
it will miss personal data that doesn't look like either.
"""
import argparse
import os
import re
import sys
from collections import Counter

PII_NAME = re.compile(
    r"(^|_)(e?mail|email_address|phone|mobile|cell|tel|fax|ssn|sin|nin|passport|"
    r"first_?name|last_?name|full_?name|name|surname|address|street|addr|zip|postcode|postal_code|"
    r"dob|birth|birthdate|date_of_birth|ip|ip_address|card|card_number|cc|iban|account_number|"
    r"license|tax_id)($|_)", re.IGNORECASE)
EMAIL = re.compile(r"^[^\s@]*@")
PHONE = re.compile(r"^\+?[\d\s().\-]{7,}$")
SSN = re.compile(r"^\d{3}-\d{2}-\d{4}$")
IPV4 = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
CARD = re.compile(r"^[\d\s\-]{13,23}$")
ALNUM_RUN = re.compile(r"[^\W_]+")
DEFAULT_LIMIT = 20
READ_ONLY = re.compile(r"^\s*(select|with|from|describe|show|summarize|table|values)\b", re.IGNORECASE)


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def looks_personal(value) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if EMAIL.match(v) or SSN.match(v) or IPV4.match(v):
        return True
    digits = re.sub(r"\D", "", v)
    if CARD.match(v) and 13 <= len(digits) <= 19 and luhn(digits):
        return True
    return bool(PHONE.match(v)) and len(digits) >= 7


def mask_run(run: str) -> str:
    if run.isdigit():
        return "#" * len(run)
    return run[0] + "*" * (len(run) - 1) if len(run) > 2 else "*" * len(run)


def mask(value) -> str:
    """Mask letters and digits, keep the separators so the value's shape survives."""
    if value is None:
        return "NULL"
    s = str(value)
    if "@" in s:
        local, _, domain = s.rpartition("@")
        # keep the domain (rarely personal, often the bug); mask the mailbox
        return ALNUM_RUN.sub(lambda m: mask_run(m.group()), local) + "@" + domain
    return ALNUM_RUN.sub(lambda m: mask_run(m.group()), s)


def shape(value) -> str:
    if value is None:
        return "NULL"
    s = re.sub(r"[A-Za-z]", "a", str(value))
    s = re.sub(r"\d", "9", s)
    return s if len(s) <= 40 else s[:40] + "..."


def personal_columns(names, rows) -> set[int]:
    flagged = {i for i, n in enumerate(names) if PII_NAME.search(n)}
    for i in range(len(names)):
        values = [r[i] for r in rows if isinstance(r[i], str)]
        if values and sum(looks_personal(v) for v in values) >= max(1, len(values) // 5):
            flagged.add(i)
    return flagged


def run_query(sql: str, db: str | None, url: str | None, limit: int | None):
    if url:
        try:
            import sqlalchemy
        except ImportError:
            raise RuntimeError("--url needs sqlalchemy: pip install sqlalchemy")
        eng = sqlalchemy.create_engine(url)
        with eng.connect() as con:
            q = f"SELECT * FROM ({sql}) AS safe_peek_q" + (f" LIMIT {limit}" if limit else "")
            res = con.execute(sqlalchemy.text(q))
            return list(res.keys()), [tuple(r) for r in res.fetchall()]
    import duckdb
    con = duckdb.connect(db or "warehouse.duckdb", read_only=True)
    try:
        rel = con.sql(sql)
        if limit:
            rel = rel.limit(limit)
        return rel.columns, rel.fetchall()
    finally:
        con.close()


def print_rows(names, rows, flagged):
    print(" | ".join(n + (" (masked)" if i in flagged else "") for i, n in enumerate(names)))
    for r in rows:
        print(" | ".join(mask(v) if i in flagged else str(v) for i, v in enumerate(r)))
    print(f"-- {len(rows)} rows shown; personal-looking columns masked: "
          + (", ".join(names[i] for i in sorted(flagged)) or "none"))


def print_profile(names, rows, flagged):
    print(f"-- profile of {len(rows)} rows (no raw values)")
    for i, n in enumerate(names):
        col = [r[i] for r in rows]
        nulls = sum(v is None for v in col)
        distinct = len(set(col))
        shapes = Counter(shape(v) for v in col if v is not None).most_common(6)
        tag = " (personal)" if i in flagged else ""
        print(f"{n}{tag}: nulls={nulls} distinct={distinct}")
        for s, c in shapes:
            print(f"    {c:>7}  {s}")


def peek(sql: str, db: str | None = None, url: str | None = None, limit: int = DEFAULT_LIMIT,
         profile: bool = False) -> tuple[int, str]:
    """(exit code, printed output) for one masked preview; used by the CLI and the MCP server."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = _peek(sql, db, url, limit, profile)
    return code, buf.getvalue()


def _peek(sql, db, url, limit, profile) -> int:
    if not READ_ONLY.match(sql) or re.search(r";\s*\S", sql):
        print("safe_peek runs one read-only query (SELECT/WITH/DESCRIBE). Use your normal runner for writes.")
        return 2
    log = os.environ.get("SAFE_PEEK_LOG")
    if log:
        import json
        import time
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "sql": sql, "via": "safe_peek"}) + "\n")
    try:
        names, rows = run_query(sql, db, url, 100_000 if profile else limit)
    except Exception as e:  # show the database error, never the data
        print(f"ERROR: {e}")
        return 1
    flagged = personal_columns(names, rows)
    (print_profile if profile else print_rows)(names, rows, flagged)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Preview query results with personal data masked.")
    ap.add_argument("sql")
    ap.add_argument("--db", help="DuckDB file (default: warehouse.duckdb)")
    ap.add_argument("--url", help="SQLAlchemy URL for other databases")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="rows to show (default 20)")
    ap.add_argument("--profile", action="store_true", help="shapes and counts only, over up to 100k rows")
    args = ap.parse_args()
    code, out = peek(args.sql, args.db, args.url, args.limit, args.profile)
    print(out, end="")
    return code


if __name__ == "__main__":
    sys.exit(main())
