---
name: spark-performance
description: Diagnose and fix slow or expensive Spark and Databricks jobs. Use when a Spark job is slow, runs out of memory, shuffles too much, has skewed tasks, when writing a join on large tables, or before running a heavy query on a cluster or SQL warehouse.
---

# Spark performance check

## Read the plan first
```python
df.explain(mode="formatted")
```
Look for: `CartesianProduct` or `BroadcastNestedLoopJoin` (an accidental cross join, so fix the join condition); `PartitionFilters: []` on a partitioned table (no pruning, so add a filter on the partition column); `Exchange` everywhere (heavy shuffles).

## Common fixes
| Symptom | Fix |
|---|---|
| Driver OOM | Remove `.collect()` / `.toPandas()` on large data; aggregate or `.limit()` first |
| Join with one small table (< ~100 MB) | `from pyspark.sql.functions import broadcast`; `big.join(broadcast(small), "key")` |
| A few tasks run far longer (skew) | Keep AQE on (`spark.sql.adaptive.enabled`, default on in Spark 3.2+) and `spark.sql.adaptive.skewJoin.enabled=true`; or salt the hot key |
| Slow Python UDF | Replace with built-in `pyspark.sql.functions`; if unavoidable, use a pandas UDF |
| Reading everything | Select only needed columns; filter on partition or clustering columns early |
| Many small files | Delta: `OPTIMIZE t`; for new tables prefer liquid clustering (`CLUSTER BY (col)`) over hand-tuned partitioning |
| Same DataFrame recomputed | `.cache()` only if reused 2+ times, then `.unpersist()` |

## Cost on Databricks
- Explore on a small or serverless SQL warehouse, not a large all-purpose cluster.
- Stop clusters you started. Set auto-termination on new ones.
- Say which compute you'll use before a heavy job.

## Verify
Compare runtime and rows before and after on the same input. A faster job that returns a different row count is a bug, not an optimization.
