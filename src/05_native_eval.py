"""Path A: native Genie benchmark evaluation via the Beta eval-runs API, results landed in Delta.

  python3 05_native_eval.py                    # full benchmark
  python3 05_native_eval.py --core-ids Q003    # selective re-evaluation: only Q003's variations
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor

from common import SCHEMA, load_state, save_state, sql, sql_str, w


def benchmark_ids(core_ids: list[str]) -> list[str]:
    rows = sql(f"""SELECT benchmark_id FROM {SCHEMA}.benchmark_map
                   WHERE core_id IN ({','.join(sql_str(c) for c in core_ids)})""")
    return [r["benchmark_id"] for r in rows]


def run_eval(space_id: str, ids: list[str] | None) -> tuple[str, float]:
    t0 = time.time()
    run = w.genie.genie_create_eval_run(space_id, benchmark_question_ids=ids)
    while True:
        r = w.genie.genie_get_eval_run(space_id, run.eval_run_id)
        status = r.eval_run_status.value if r.eval_run_status else None
        print(f"{time.time() - t0:5.0f}s {status} {r.num_done}/{r.num_questions}", flush=True)
        if status not in ("RUNNING", "NOT_STARTED"):
            return run.eval_run_id, time.time() - t0
        time.sleep(20)


def fetch_results(space_id: str, run_id: str) -> list[dict]:
    results, token = [], None
    while True:
        page = w.genie.genie_list_eval_results(space_id, run_id, page_size=100, page_token=token)
        results += page.eval_results or []
        token = page.next_page_token
        if not token:
            break
    with ThreadPoolExecutor(8) as pool:
        details = list(pool.map(
            lambda r: w.genie.genie_get_eval_result_details(space_id, run_id, r.result_id).as_dict(), results))
    return details


def persist(run_id: str, scope: str, details: list[dict]) -> None:
    sql(f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.native_eval_results (
              eval_run_id STRING, run_ts TIMESTAMP, scope STRING, test_id STRING, core_id STRING,
              benchmark_id STRING, assessment STRING, assessment_reasons STRING,
              genie_sql STRING, gt_sql STRING, genie_exec_state STRING, gt_exec_state STRING)""")
    first = lambda d, k: (d.get(k) or [{}])[0]
    vals = []
    for d in details:
        act, exp = first(d, "actual_response"), first(d, "expected_response")
        vals.append("(" + ",".join([
            sql_str(run_id), "current_timestamp()", sql_str(scope), "NULL", "NULL",
            sql_str(d.get("benchmark_question_id")), sql_str(d.get("assessment")),
            sql_str(",".join(d.get("assessment_reasons") or [])),
            sql_str(act.get("response")), sql_str(exp.get("response")),
            sql_str(((act.get("sql_execution_result") or {}).get("status") or {}).get("state")),
            sql_str(((exp.get("sql_execution_result") or {}).get("status") or {}).get("state"))]) + ")")
    sql(f"INSERT INTO {SCHEMA}.native_eval_results VALUES {','.join(vals)}")
    # attach test_id / core_id from the benchmark map
    sql(f"""MERGE INTO {SCHEMA}.native_eval_results t USING {SCHEMA}.benchmark_map m
            ON t.benchmark_id = m.benchmark_id AND t.eval_run_id = {sql_str(run_id)}
            WHEN MATCHED THEN UPDATE SET t.test_id = m.test_id, t.core_id = m.core_id""")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core-ids", nargs="*")
    args = ap.parse_args()
    space_id = load_state()["space_id"]
    ids = benchmark_ids(args.core_ids) if args.core_ids else None
    scope = "core:" + ",".join(args.core_ids) if args.core_ids else "full"
    print(f"scope={scope} questions={len(ids) if ids else 'all'}")
    run_id, secs = run_eval(space_id, ids)
    details = fetch_results(space_id, run_id)
    persist(run_id, scope, details)
    save_state(**{f"native_run_{scope}": {"eval_run_id": run_id, "seconds": round(secs), "n": len(details)}})
    print(f"run {run_id}: {len(details)} results in {secs:.0f}s ({len(details) / (secs / 60):.1f} q/min)")
    for r in sql(f"""SELECT core_id, count(*) n, count_if(assessment = 'GOOD') good, count_if(assessment = 'BAD') bad,
                            count_if(assessment NOT IN ('GOOD', 'BAD')) other
                     FROM {SCHEMA}.native_eval_results WHERE eval_run_id = {sql_str(run_id)}
                     GROUP BY core_id ORDER BY core_id"""):
        print(r)


if __name__ == "__main__":
    main()
