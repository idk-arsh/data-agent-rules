# data-agent-rules (Claude Code plugin)

Keeps Claude from scanning, overwriting, or inventing data when it works on SQL, Spark, dbt, Databricks, Snowflake, BigQuery or DuckDB. It loads eight short data rules into every session, asks you before destructive SQL runs and before BigQuery queries over budget, and gives Claude a way to look at data without pasting personal information into the chat.

## What it contains

- **Rules** (`rules.md`): printed into each session by a `SessionStart` hook (`cat rules.md`).
- **Destructive-SQL seatbelt** (`hooks/sql_guard.py`): a `PreToolUse` hook on Bash. It reads the command Claude is about to run and, if it contains `DROP`, `TRUNCATE`, `DELETE`/`UPDATE` without `WHERE`, `CREATE OR REPLACE TABLE`, `INSERT OVERWRITE`, `ALTER TABLE ... DROP`, `mode("overwrite")` or `dbt --full-refresh`, asks you to confirm. Turn it off with `DATA_AGENT_RULES_GUARD=off`.
- **Personal-data guard** (`hooks/pii_guard.py`): a `PreToolUse` hook on Bash that asks before a command selects raw values from columns or tables that look personal (email, phone, name, address and similar) and suggests `safe_peek` instead. Turn it off with `DATA_AGENT_RULES_PII=off`.
- **BigQuery cost guard** (`hooks/cost_guard.py`): a `PreToolUse` hook on Bash. Before `bq query` runs, it runs the same query as a free `bq query --dry_run` and asks you if it would bill more than 100GB (`DATA_AGENT_RULES_MAX_SCAN`), showing the size and the dollar estimate. Queries with `--dry_run` or `--maximum_bytes_billed` pass. Turn it off with `DATA_AGENT_RULES_COST=off`.
- **cost_check** (`tools/cost_check.py`): prices a query before it runs. BigQuery dry run with dollars; Snowflake `EXPLAIN` bytes and micro-partitions; Databricks `EXPLAIN COST`.
- **safe_peek** (`tools/safe_peek.py`): a read-only query preview with a row limit that masks personal-looking values while keeping their shape, so errors stay visible.
- **Skills**: `explore-data`, `safe-writes`, `dbt-changes`, `spark-performance`, `test-pipelines`.

## Data handling

Everything runs locally with Python. The hooks read the command text Claude Code passes them and print a decision; they store nothing and send nothing to us or anyone else. The SQL and PII hooks make no network calls. The cost guard runs one `bq query --dry_run` with your own `bq` login, which sends the query text to your BigQuery project (dry runs are free and read no data). `safe_peek` and `cost_check` connect only to the database you point them at.

Source, eval and results: https://github.com/idk-arsh/data-agent-rules. MIT license.
