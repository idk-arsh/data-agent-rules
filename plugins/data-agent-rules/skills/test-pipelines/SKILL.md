---
name: test-pipelines
description: Add tests and data checks to data pipelines. Use when writing or changing a PySpark transformation, SQL model or data pipeline, when asked to add tests, or before saying a data change works.
---

# Testing data pipelines

## Unit test transformations on tiny inputs (PySpark)
```python
# conftest.py
import pytest
from pyspark.sql import SparkSession

@pytest.fixture(scope="session")
def spark():
    return SparkSession.builder.master("local[1]").appName("tests").getOrCreate()
```
```python
# test_transform.py
from chispa import assert_df_equality
from mypipeline import dedupe_orders

def test_dedupe_keeps_latest(spark):
    src = spark.createDataFrame(
        [(1, "2026-01-01", "new"), (1, "2026-01-02", "shipped"), (2, "2026-01-01", "new")],
        ["order_id", "updated_at", "status"])
    expected = spark.createDataFrame(
        [(1, "2026-01-02", "shipped"), (2, "2026-01-01", "new")],
        ["order_id", "updated_at", "status"])
    assert_df_equality(dedupe_orders(src), expected, ignore_row_order=True)
```
Cover: duplicates, nulls in keys, empty input, late-arriving rows, and a re-run (idempotency).

## Data checks on real tables (any SQL engine)
Each query should return 0 rows:
```sql
-- keys unique
SELECT order_id FROM dev.orders GROUP BY order_id HAVING COUNT(*) > 1;
-- required fields present
SELECT * FROM dev.orders WHERE order_id IS NULL OR order_date IS NULL LIMIT 10;
-- no orphans
SELECT o.order_id FROM dev.orders o LEFT JOIN dev.customers c USING (customer_id)
WHERE c.customer_id IS NULL LIMIT 10;
```
In dbt these are `unique`, `not_null` and `relationships` tests.

## Before/after comparison for any change
Report in a table: row count, distinct keys, null counts on changed columns, and sums of key measures (revenue, quantity), before vs after. Explain every difference. "Tests pass" is not a report; the numbers are.
