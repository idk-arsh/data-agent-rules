---
name: safe-writes
description: Write, update, deduplicate or delete table data safely and idempotently. Use before any INSERT, MERGE, UPDATE, DELETE, overwrite, DROP or table rebuild, when loading incremental data, or when a job may be retried (Delta, Snowflake, BigQuery, DuckDB).
---

# Safe writes

## Before any destructive statement
1. Run the same predicate as a count: `SELECT COUNT(*) FROM t WHERE <predicate>`. Show the number.
2. Name the undo:
   | Engine | Undo |
   |---|---|
   | Delta | `DESCRIBE HISTORY t`, then `RESTORE TABLE t TO VERSION AS OF <n>` |
   | Snowflake | `SELECT ... AT(OFFSET => -3600)`, `UNDROP TABLE t`; or back up first with `CREATE TABLE t_backup CLONE t` |
   | BigQuery | `SELECT ... FOR SYSTEM_TIME AS OF TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)`; snapshot with `CREATE SNAPSHOT TABLE` |
   | DuckDB | no time travel; `CREATE TABLE t_backup AS SELECT * FROM t` first |
3. Target a table in prod, main or gold, or one you did not create? Ask first.

## Idempotent loads: MERGE on a key, de-duplicated source
De-duplicate the source first, keeping the latest row per key:
```sql
WITH src AS (
  SELECT * FROM staging
  QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC) = 1
)
```
(BigQuery requires a `WHERE`, `GROUP BY` or `HAVING` alongside `QUALIFY`; add `WHERE TRUE`.)

**Delta / Databricks**
```sql
MERGE INTO dev.orders t
USING src s ON t.order_id = s.order_id
WHEN MATCHED AND s.updated_at > t.updated_at THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
```
**Snowflake:** same shape; list columns: `UPDATE SET t.status = s.status ...`, `INSERT (order_id, status) VALUES (s.order_id, s.status)`.
**BigQuery:** same shape; `WHEN NOT MATCHED THEN INSERT ROW`; `UPDATE SET` lists columns.
**DuckDB:** `MERGE INTO` (1.4+), or `INSERT INTO t SELECT * FROM src ON CONFLICT (order_id) DO UPDATE SET status = EXCLUDED.status` (needs a primary key or unique constraint).

Check: running the load twice must leave the row count unchanged. Verify with `SELECT order_id, COUNT(*) FROM t GROUP BY 1 HAVING COUNT(*) > 1`.

## Replacing part of a table
Overwrite only the affected partitions:
```python
(df.write.format("delta").mode("overwrite")
   .option("replaceWhere", "order_date >= '2026-09-01' AND order_date < '2026-10-01'")
   .saveAsTable("dev.orders"))
```
Spark with Hive-style partitions: set `spark.sql.sources.partitionOverwriteMode=dynamic` before `mode("overwrite")`, or a full overwrite wipes every partition.

## Deduplicating a table in place
Don't `DELETE` from prod. Build the clean table next to it, compare counts, then swap after confirmation:
```sql
CREATE TABLE dev.orders_dedup AS
SELECT * FROM prod.orders
QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC) = 1;
-- report: rows before, rows after, duplicates removed
```

## Never
- `DELETE` or `UPDATE` without `WHERE`.
- `CREATE OR REPLACE TABLE` on a table someone else owns.
- Blind `append` or `INSERT` in a job that can retry.
