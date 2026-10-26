"""Create the POC Genie agent (first run) or sync benchmarks into it (subsequent runs).

Ground truth is read from core_questions and inherited by every valid variation, so a single
gt_sql update at core_id level propagates to all of its variations on the next sync.
Only the `benchmarks` section of serialized_space is replaced; data sources and instructions are kept.
"""
import hashlib
import json
import sys

from common import SCHEMA, WAREHOUSE_ID, bench_id, load_state, save_state, sql, sql_str, w

MAX_BENCHMARKS = 500  # documented per-agent limit


def build_benchmarks(core_ids: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    where = f"AND v.core_id IN ({','.join(sql_str(c) for c in core_ids)})" if core_ids else ""
    rows = sql(f"""
        SELECT v.test_id, v.core_id, v.question, v.arguments, c.gt_sql
        FROM {SCHEMA}.variations v JOIN {SCHEMA}.core_questions c USING (core_id)
        WHERE v.status = 'valid' {where}
        ORDER BY v.test_id""")
    if len(rows) > MAX_BENCHMARKS:
        raise SystemExit(f"{len(rows)} benchmarks > {MAX_BENCHMARKS}; sync in batches (by core_id) instead")
    questions, mapping = [], []
    for r in rows:
        text = r["question"] + (f" (context: {r['arguments']})" if r["arguments"] else "")
        bid = bench_id(r["test_id"])
        questions.append({"id": bid, "question": [text],
                          "answer": [{"format": "SQL", "content": [r["gt_sql"]]}]})
        mapping.append({"test_id": r["test_id"], "core_id": r["core_id"], "benchmark_id": bid,
                        "gt_sql_hash": hashlib.md5(r["gt_sql"].encode()).hexdigest()})
    return sorted(questions, key=lambda q: q["id"]), mapping


def initial_space(benchmarks: list[dict]) -> dict:
    rx, reps = f"{SCHEMA}.rx_weekly", f"{SCHEMA}.rep_territory"
    return {
        "version": 2,
        "config": {"sample_questions": sorted([
            {"id": "a0000000000000000000000000000001", "question": ["What were total NRx by brand in 2026-Q2?"]},
            {"id": "a0000000000000000000000000000002", "question": ["Which region had the highest Zorvela NRx in 2026-Q3?"]},
        ], key=lambda x: x["id"])},
        "data_sources": {"tables": sorted([
            {"identifier": rx, "column_configs": sorted([
                {"column_name": "brand", "enable_entity_matching": True, "enable_format_assistance": True},
                {"column_name": "region", "enable_entity_matching": True, "enable_format_assistance": True},
                {"column_name": "trx", "description": ["Total prescriptions (TRx)"], "synonyms": ["TRx", "total scripts"]},
                {"column_name": "nrx", "description": ["New prescriptions (NRx)"], "synonyms": ["NRx", "new scripts"]},
            ], key=lambda c: c["column_name"])},
            {"identifier": reps},
        ], key=lambda t: t["identifier"])},
        "instructions": {
            # Deliberately incomplete: market share is NOT defined, to produce realistic failures for diagnosis.
            "text_instructions": [{"id": "b0000000000000000000000000000001", "content": [
                "TRx means total prescriptions: use SUM(trx). NRx means new prescriptions: use SUM(nrx).\n",
                "Quarters are stored in the quarter column as 'YYYY-Qn', e.g. '2026-Q2'.\n",
                "If the question includes context sales_rep_id = X, then I/me/my refers to that sales rep: filter sales_rep_id = 'X'.\n",
            ]}],
            "join_specs": [{"id": "c0000000000000000000000000000001",
                            "left": {"identifier": rx, "alias": "rx_weekly"},
                            "right": {"identifier": reps, "alias": "rep_territory"},
                            "sql": ["`rx_weekly`.`sales_rep_id` = `rep_territory`.`sales_rep_id`",
                                    "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--"]}],
        },
        "benchmarks": {"questions": benchmarks},
    }


def write_mapping(mapping: list[dict]) -> None:
    sql(f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.benchmark_map (
              test_id STRING, core_id STRING, benchmark_id STRING, gt_sql_hash STRING, synced_at TIMESTAMP)""")
    values = ",".join(f"({sql_str(m['test_id'])},{sql_str(m['core_id'])},{sql_str(m['benchmark_id'])},"
                      f"{sql_str(m['gt_sql_hash'])},current_timestamp())" for m in mapping)
    sql(f"""MERGE INTO {SCHEMA}.benchmark_map t
            USING (SELECT * FROM VALUES {values} AS s(test_id, core_id, benchmark_id, gt_sql_hash, synced_at)) s
            ON t.test_id = s.test_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *""")


def main() -> None:
    benchmarks, mapping = build_benchmarks()
    state = load_state()
    if "space_id" not in state:
        space = w.genie.create_space(
            warehouse_id=WAREHOUSE_ID, serialized_space=json.dumps(initial_space(benchmarks)),
            title="[POC] Pharma Commercial Rx - Genie Eval Framework",
            description="Reference implementation (synthetic data). Benchmarks synced from Delta core_questions/variations.")
        state = save_state(space_id=space.space_id)
        print(f"created space {space.space_id}")
    else:
        current = w.genie.get_space(state["space_id"], include_serialized_space=True)
        payload = json.loads(current.serialized_space)
        payload["benchmarks"] = {"questions": benchmarks}
        w.genie.update_space(state["space_id"], serialized_space=json.dumps(payload))
        print(f"updated benchmarks on space {state['space_id']}")
    write_mapping(mapping)
    synced = json.loads(w.genie.get_space(state["space_id"], include_serialized_space=True).serialized_space)
    print(f"benchmarks in space: {len(synced.get('benchmarks', {}).get('questions', []))} (expected {len(benchmarks)})")
    print(f"text instructions preserved: {bool(synced.get('instructions', {}).get('text_instructions'))}")


if __name__ == "__main__":
    sys.exit(main())
