"""Bulk ground-truth update demo: update ground truth ONCE at core_id level -> propagate to all variations -> re-run only that core.

Scenario: the business redefines market share to be reported as a percentage, so Q003's ground truth is outdated.
"""
import subprocess
import sys

from common import SCHEMA, sql, sql_str

CORE_ID = "Q003"
NEW_GT = (f"SELECT 100 * SUM(trx) / SUM(market_trx) AS market_share_pct FROM {SCHEMA}.rx_weekly "
          "WHERE brand = 'ZORVELA' AND region = 'Northeast' AND quarter = '2026-Q2'")

before = {r["test_id"]: r["gt_sql_hash"] for r in sql(f"SELECT test_id, gt_sql_hash FROM {SCHEMA}.benchmark_map")}

# 1. single-point update, attributed and explained (Delta history + CDF give the audit trail)
sql(f"""UPDATE {SCHEMA}.core_questions
        SET gt_sql = {sql_str(NEW_GT)}, updated_by = current_user(),
            update_reason = 'business rule change: market share reported as percentage'
        WHERE core_id = {sql_str(CORE_ID)}""")

# 2. propagate atomically to the Genie agent (only the benchmarks section of serialized_space is replaced)
subprocess.run([sys.executable, "04_sync_benchmarks.py"], check=True)

after = {r["test_id"]: r["gt_sql_hash"] for r in sql(f"SELECT test_id, gt_sql_hash FROM {SCHEMA}.benchmark_map")}
changed = sorted(t for t in after if before.get(t) != after[t])
print(f"variations whose ground truth changed: {len(changed)} -> {changed}")
assert all(t.startswith(CORE_ID) for t in changed), "propagation leaked outside the core question"

# 3. audit trail
for r in sql(f"""SELECT _change_type, core_id, updated_by, update_reason, _commit_version, _commit_timestamp
                 FROM table_changes('{SCHEMA}.core_questions', 1)
                 WHERE core_id = {sql_str(CORE_ID)} ORDER BY _commit_version"""):
    print(r)

# 4. selective re-evaluation: only this core question's variations
subprocess.run([sys.executable, "05_native_eval.py", "--core-ids", CORE_ID], check=True)
