# Genie evaluation at scale: reference implementation

This folder holds the SQL and Python behind the [walkthrough deck](../docs/index.html). It implements a SQL-grounded evaluation framework for Genie agents natively on Databricks:

- core questions with a single copy of ground-truth SQL
- generated and validated variations
- two evaluation paths: native benchmark evaluation and an MLflow harness
- failure triage with `ai_decide()`
- fix recommendations through Unity Gateway
- core-question health
- bulk ground-truth updates with selective re-evaluation

> **Provided as-is.** This is a reference implementation for discussion. It is not a Databricks product, is not supported, and has not been hardened for production. All data is synthetic. `ai_decide()` and the Genie benchmark evaluation API are **Beta** and may change.

Workspace-specific names (catalog, schema, warehouse, CLI profile) are settings, not hard-coded values. Brand names in the synthetic data are fictional.

## Prerequisites

- **A Unity Catalog schema** where you can create tables, views, and a model service.
- **A serverless or pro SQL warehouse.**
- **The `ai_decide()` preview**, enabled by a workspace admin on the Previews page.
- **Access to a Databricks-hosted model** for `ai_query()`. The default is `system.ai.claude-sonnet-4-6`. Check which `system.ai` models your workspace offers; not every model supports batch `ai_query()`.
- **Permission to create a Genie agent**, plus MLflow experiment access.
- **Python 3.10+.**
- **A Databricks CLI profile** for the workspace. Run `databricks auth login` if you don't have one.

## Setup

```bash
pip install -r requirements.txt

export DATABRICKS_CONFIG_PROFILE=<your-profile>
export GENIE_EVAL_SCHEMA=<catalog>.<schema>
export GENIE_EVAL_WAREHOUSE_ID=<warehouse-id>
# optional: export GENIE_EVAL_MODEL=system.ai.<model>
```

The harness (step 06) calls a **Unity Gateway model service** named `genie_eval_llm` in the same schema. Create it once and point it at a hosted model:

```bash
databricks ai-gateway create-model-service schemas/<catalog>.<schema> genie_eval_llm --json '{
  "comment": "Governed LLM for Genie evaluation",
  "config": {"routing": {"destinations": [
    {"name": "primary",
     "destination_type": "DESTINATION_TYPE_PAY_PER_TOKEN_FOUNDATION_MODEL",
     "pay_per_token_config": {"model": "models/system.ai.databricks-claude-sonnet-4-6"},
     "traffic_percentage": 100}
  ]}}
}'
```

To use your own LLM gateway instead, register it as a **Custom model provider service** (endpoint URL plus a bearer token or header), then point the destination at it.

## Run order

Run every command from this folder.

- `run_sql.py` fills in the `{{schema}}` and `{{model}}` placeholders in the SQL files. Use `--print` to see the SQL without running it.
- Step 04 creates the Genie agent and saves its ID to `state.json`. Every later step reads it from there.

| Deck step | Command | Creates or changes |
|---|---|---|
| 01 | `python run_sql.py 01a_synthetic_data.sql 01b_synthetic_data.sql` | `rep_territory`, `rx_weekly` |
| 01 | `python run_sql.py 02a_core_questions.sql 02b_core_questions.sql` | `core_questions`, with Change Data Feed |
| 02 | `python run_sql.py 03a_generate_variations.sql` | `variations_raw` |
| 03 | `python run_sql.py 03b_validate_variations.sql` | `variations` (valid, invalid, needs review) |
| 04 | `python 04_sync_benchmarks.py` | Genie agent on first run; benchmarks on every run; `benchmark_map` |
| 05 | `python 05_native_eval.py` | `native_eval_results` |
| 06, 07 | `python 06_custom_harness.py --run-name harness-full` | MLflow run and traces; `harness_results` |
| 08 | `python run_sql.py 07_triage_native.sql` | `native_diagnostics` |
| 09 | `python run_sql.py 07b_recommendations.sql` | `native_recommendations` |
| 10 | `python run_sql.py 08b_core_health_current.sql`, then deploy the dashboard (below) | `core_health_current` view |
| 11 | `python 09_bulk_gt_update.py` | Updates Q003's ground truth, re-syncs, and re-runs only Q003 natively |
| 12 | `python 11_apply_instruction_fix.py`, then `python 06_custom_harness.py --core-ids Q007 --run-name harness-Q007-after-fix` | Appends one instruction, then re-runs Q007 only |
| optional | `python run_sql.py 12_classifier_comparison.sql` | `classifier_comparison` |

**Options:**
- `05_native_eval.py` and `06_custom_harness.py` both take `--core-ids Q003 Q007 …` for selective runs.
- `06_custom_harness.py` also takes `--workers` (default 4) and `--limit`.

### Dashboard (step 10)

```bash
databricks lakeview create --display-name "Genie Benchmark Health" \
  --warehouse-id "$GENIE_EVAL_WAREHOUSE_ID" \
  --dataset-catalog <catalog> --dataset-schema <schema> \
  --serialized-dashboard "$(cat 10_dashboard.json)"
databricks lakeview publish <dashboard-id> --warehouse-id "$GENIE_EVAL_WAREHOUSE_ID"
```

## Adapting it to your agents

**Use an evaluation copy of the agent.** The sync in step 04 replaces the agent's whole benchmark list. Point it at a copy of your agent, never at the production agent, by putting the copy's ID in `state.json` as `space_id`.

**Data and questions.**
- Replace `01a` and `01b` with your tables, and `02b` with your core questions and their ground-truth SQL.
- In `04_sync_benchmarks.py`, `initial_space()` defines the demo agent's tables and instructions.

**Runtime arguments.** Arguments such as `sales_rep_id` are appended to the question as `(context: …)` in `04_sync_benchmarks.py` and `06_custom_harness.py`. Change this to match how you pass them today.

**Thresholds.**

| File | Rule |
|---|---|
| `03b_validate_variations.sql` | **valid:** P(equivalent) ≥ 0.8 and P(ambiguous) < 0.5. **invalid:** P(equivalent) < 0.3. |
| `07_triage_native.sql`, and the `ds_failures` dataset in `10_dashboard.json` for harness rows | **likely false negative:** P(equivalent) ≥ 0.8. **human review:** confidence < 0.6. |
| `08b_core_health_current.sql` | **Healthy:** pass rate ≥ 0.8. |

**Comparison rules.** Rounding, text normalization, and the definition of a partial match are set by `_norm()` and `compare()` in `06_custom_harness.py`. Version them with the benchmark.

**Orchestration.** Each step maps to one Lakeflow Jobs task:
generate → validate → sync → evaluate → triage → recommend → report.

## Things to plan for

- **500 benchmarks per agent.** The native path holds up to 500 benchmarks per agent, and `04_sync_benchmarks.py` enforces the limit. For larger sets, rotate batches by core question or use several evaluation copies. The MLflow harness has no cap.
- **Native evaluation speed.** We observed about 3 questions per minute. Run the full suite on a schedule and use selective runs for changes.
- **Conversation API throughput.** It is limited per workspace and shared by every Genie agent there. The harness retries with exponential backoff and runs ground truth once per core question. Size throughput for full runs with your Databricks account team.
- **Capacity used by evaluation runs.** Each run uses workspace capacity and stores results. Prefer fewer, larger runs, put several core questions into one selective run, and delete old runs from the agent's Benchmarks tab.
- **Evaluation API scope.** The benchmark evaluation API covers chat mode. Use the MLflow harness for agent mode.
- **Run-to-run variance.** Genie answers vary slightly between identical runs. Judge health per core question across its variations, and track the trend over runs.
- **`ai_query()` with Claude models.** These models reject `responseFormat => '{"type":"json_object"}'`. The SQL here asks for JSON in the prompt and extracts it with `regexp_extract`.
- **SQL quoting.** Write quotes inside ground-truth SQL as `\'`, because Spark SQL reads `''` as two adjacent literals. Execute every ground-truth query before syncing it.
