---
name: dbt-changes
description: Change dbt models, tests or sources safely. Use when editing a dbt model, adding a column, fixing a failing dbt test, refactoring SQL in a dbt project, or when asked to run dbt.
---

# Changing a dbt project

## Before editing
- Find what depends on the model: `dbt ls --select model_name+`.
- Read the model's YAML: tests, contract, column docs. A model with `contract: {enforced: true}` breaks downstream consumers if a column changes type or disappears.
- Look at real rows: `dbt show --select model_name --limit 20`.

## While editing
- Reference other models with `{{ ref('...') }}` and sources with `{{ source('...', '...') }}`. Never hardcode schema names.
- Look up columns in `target/manifest.json` or the upstream model's SQL. Don't guess them.
- Adding a column: add it to the YAML with a description, plus `not_null` / `unique` / `accepted_values` tests where they apply.
- Incremental models: keep the `is_incremental()` filter and the `unique_key`. Changing either needs a full refresh, so ask before running it.

## Build only what changed
```bash
dbt build --select state:modified+ --defer --state path/to/prod-artifacts
```
Without prod artifacts: `dbt build --select model_name+`.
Never run `dbt run` with no selector, `--full-refresh`, or `--target prod` unless the user asks for exactly that.

## Prove it
- Every changed model gets tests passing in `dbt build`.
- For logic changes, add a unit test (dbt 1.8+):
```yaml
unit_tests:
  - name: test_order_status_mapping
    model: fct_orders
    given:
      - input: ref('stg_orders')
        rows:
          - {order_id: 1, status_code: "S"}
          - {order_id: 2, status_code: "X"}
    expect:
      rows:
        - {order_id: 1, status: "shipped"}
        - {order_id: 2, status: "unknown"}
```
- Report before/after: row count, distinct keys, and null counts on the changed columns, from `dbt show` or a query against the dev target.
