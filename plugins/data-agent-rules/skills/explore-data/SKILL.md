---
name: explore-data
description: Profile an unfamiliar table cheaply before querying it in full. Use when starting work on a table, answering a question about data you have not seen yet, estimating query cost, or when a table may be large (Databricks, Spark, Snowflake, BigQuery, DuckDB).
---

# Explore data without scanning it

Work in this order and stop as soon as you know enough.

## 1. Schema and size, no data read
| Engine | Schema | Size |
|---|---|---|
| Databricks / Delta | `DESCRIBE TABLE t` | `DESCRIBE DETAIL t` (numFiles, sizeInBytes) |
| Spark DataFrame | `df.printSchema()` | `spark.table("t").inputFiles()` count, or catalog stats |
| Snowflake | `DESCRIBE TABLE t` | `SELECT row_count, bytes FROM information_schema.tables WHERE table_name = 'T'` |
| BigQuery | `bq show --schema dataset.t` | `bq show dataset.t` (numRows, numBytes); `bq query --dry_run --use_legacy_sql=false '<sql>'` gives bytes scanned before you run |
| DuckDB | `DESCRIBE t` | `SELECT estimated_size FROM duckdb_tables() WHERE table_name = 't'` |
| dbt | `dbt ls --select model --output json`, column docs in `target/manifest.json` | |

## 2. A small sample
| Engine | Sample |
|---|---|
| Databricks SQL | `SELECT * FROM t TABLESAMPLE (100 ROWS)` or `LIMIT 100` |
| PySpark | `spark.table("t").limit(100).show()` (never `.collect()` on the whole table) |
| Snowflake | `SELECT * FROM t SAMPLE (100 ROWS)` |
| BigQuery | `SELECT * FROM t TABLESAMPLE SYSTEM (1 PERCENT) LIMIT 100` (`LIMIT` alone still scans every selected column) |
| DuckDB | `SELECT * FROM t USING SAMPLE 100` |
| dbt | `dbt show --select model --limit 20` |

If the table is partitioned, sample one recent partition: `WHERE event_date = current_date() - 1`.

## 3. Profile the columns you need
One query, aggregates only:
```sql
SELECT
  COUNT(*)                      AS rows,
  COUNT(DISTINCT customer_id)   AS distinct_keys,
  COUNT(*) - COUNT(customer_id) AS null_keys,
  MIN(created_at), MAX(created_at)
FROM t
WHERE created_at >= current_date() - 7   -- keep a filter on big tables
```
Check key uniqueness before any join: `SELECT key, COUNT(*) FROM t GROUP BY key HAVING COUNT(*) > 1 LIMIT 10`.

## 4. Say what the full query will cost
Before running the real query, state the table size, the filters that prune it, and (BigQuery) the dry-run bytes. If it scans more than a few GB, or the table has no partition filter, say so and ask.

## Personal data
If columns look like names, emails, phones or IDs, show counts and patterns, not raw rows. For examples, mask them, e.g. in Spark SQL: `regexp_replace(email, '(^.).*(@.*$)', '$1***$2')`.
