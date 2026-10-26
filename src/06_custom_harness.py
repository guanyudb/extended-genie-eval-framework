"""Path B: custom Genie evaluation harness with no 500-benchmark cap.

Genie Conversation API -> mlflow.genai.evaluate with three scorers:
  * result_match      - deterministic result-set comparison vs ground truth (code scorer)
  * ai_decide triage  - failure_category / semantic equivalence / severity with confidences (SQL ai_decide)
  * recommended_fix   - instruction fix suggestion via the Unity Gateway model service
Results also land in Delta (harness_results) for core_id-level health aggregation.

Run: uv run --with "mlflow[databricks]>=3.4" --with databricks-sdk python 06_custom_harness.py [--core-ids Q003 ...] [--limit N]
"""
import argparse
import json
import os
import time
from collections import Counter
from decimal import Decimal, InvalidOperation

import mlflow
from mlflow.entities import Feedback
from mlflow.genai.scorers import scorer

from common import PROFILE, SCHEMA, WAREHOUSE_ID, load_state, sql, sql_str, w

MODEL_SERVICE = f"{SCHEMA}.genie_eval_llm"  # Unity Gateway model service: a hosted model, or your own gateway as a Custom provider
EXPERIMENT = f"/Users/{w.current_user.me().user_name}/genie_eval_poc"
SPACE_ID = load_state()["space_id"]
MAX_ROWS = 200

FAILURE_CRITERIA = {
    "missing_or_wrong_filter": "A required filter is missing, or a filter column/value is wrong",
    "wrong_aggregation": "Wrong aggregation function, or aggregates at the wrong level",
    "wrong_join": "Missing, extra or incorrect join",
    "wrong_metric_definition": "Computes a different business metric or formula than the ground truth (e.g. wrong ratio)",
    "time_logic": "Wrong time period, date range, or period-comparison logic",
    "granularity_only": "Same numbers but different grouping, extra/missing columns, ordering or LIMIT only",
    "wrong_table_or_column": "Uses the wrong table or column",
    "misinterpretation": "Misunderstood the intent of the question",
    "execution_error": "Genie SQL failed or returned no result",
}
DECIDE_QUESTIONS = json.dumps({
    "semantically_equivalent": {"type": "noul", "instructions":
        "Would a business user get the same answer from the Genie result as from the ground-truth result, "
        "ignoring column names, column order, row order and rounding?"},
    "failure_category": {"type": "choice", "instructions":
        "Compare the Genie SQL with the ground-truth SQL. What is the primary reason they differ?",
        "criteria": FAILURE_CRITERIA},
    "severity": {"type": "score", "instructions": "How harmful is this difference for a business user?",
                 "criteria": ["Cosmetic: answer still usable", "Partially wrong: usable only with caveats",
                              "Wrong: misleading answer"]},
})


# ---------- Genie call (traced) ----------
@mlflow.trace(span_type="TOOL")
def ask_genie(question: str) -> dict:
    for attempt in range(6):
        try:
            msg = w.genie.start_conversation_and_wait(SPACE_ID, question)
            break
        except Exception as e:  # rate limits / transient errors: exponential backoff
            if attempt == 5:
                return {"status": "ERROR", "error": str(e)[:500], "genie_sql": None, "columns": [], "rows": []}
            time.sleep(min(60, 2 ** attempt * 3))
    out = {"status": msg.status.value if msg.status else None, "genie_sql": None, "columns": [], "rows": [],
           "error": msg.error.error if msg.error else None, "text": None,
           "conversation_id": msg.conversation_id, "message_id": msg.message_id}
    for att in msg.attachments or []:
        if att.text and att.text.content:
            out["text"] = att.text.content
        if att.query and att.query.query:
            out["genie_sql"] = att.query.query
            res = w.genie.get_message_attachment_query_result(SPACE_ID, msg.conversation_id, msg.message_id,
                                                              att.attachment_id)
            sr = res.statement_response
            if sr and sr.manifest and sr.result:
                out["columns"] = [c.name for c in sr.manifest.schema.columns]
                out["rows"] = (sr.result.data_array or [])[:MAX_ROWS]
    return out


@mlflow.trace(span_type="AGENT")
def predict_fn(question: str, test_id: str, core_id: str) -> dict:
    t0 = time.time()
    out = ask_genie(question)
    out["latency_s"] = round(time.time() - t0, 1)
    return out


# ---------- result comparison ----------
def _norm(v):
    if v is None:
        return "null"
    try:
        return str(round(Decimal(str(v)), 1).normalize())
    except InvalidOperation:
        return str(v).strip().lower()


def compare(gt_rows: list, genie_rows: list) -> str:
    gt = Counter(tuple(sorted(_norm(v) for v in r)) for r in gt_rows)
    ge = Counter(tuple(sorted(_norm(v) for v in r)) for r in genie_rows)
    if gt == ge:
        return "match"
    if len(gt_rows) == len(genie_rows) and gt_rows:
        # Genie returned the GT values plus extra columns (e.g. extra dimension/label) -> partial
        ge_sets = [set(_norm(v) for v in r) for r in genie_rows]
        if all(any(set(_norm(v) for v in g) <= s for s in ge_sets) for g in gt_rows):
            return "partial"
    return "mismatch"


@scorer
def result_match(outputs: dict, expectations: dict) -> list[Feedback]:
    if not outputs.get("genie_sql") or outputs.get("status") != "COMPLETED":
        label = "error"
    else:
        label = compare(expectations["gt_rows"], outputs["rows"])
    return [Feedback(name="result_match", value=label,
                     rationale=f"gt_rows={len(expectations['gt_rows'])} genie_rows={len(outputs.get('rows') or [])}"),
            Feedback(name="passed", value=label in ("match", "partial"))]


# ---------- ai_decide triage (only for non-matching rows) ----------
def triage(inputs: dict, outputs: dict, expectations: dict) -> dict | None:
    if compare(expectations["gt_rows"], outputs.get("rows") or []) == "match" and outputs.get("genie_sql"):
        return None
    state = json.dumps({"question": inputs["question"], "ground_truth_sql": expectations["gt_sql"],
                        "genie_sql": outputs.get("genie_sql") or "(none)", "genie_error": outputs.get("error"),
                        "ground_truth_result_sample": expectations["gt_rows"][:10],
                        "genie_result_sample": (outputs.get("rows") or [])[:10],
                        "genie_columns": outputs.get("columns")})
    row = sql(f"SELECT to_json(ai_decide({sql_str(state)}, {sql_str(DECIDE_QUESTIONS)})) AS d")[0]
    return json.loads(row["d"])


@scorer
def failure_triage(inputs: dict, outputs: dict, expectations: dict) -> list[Feedback]:
    d = triage(inputs, outputs, expectations)
    if d is None:
        return [Feedback(name="failure_category", value="none")]
    if d.get("error_message"):
        return [Feedback(name="failure_category", value="triage_error", rationale=d["error_message"])]
    a = d["response"]["answers"]
    fc, sev = a["failure_category"], a["severity"]
    return [
        Feedback(name="failure_category", value=fc["choice"],
                 rationale=f"confidence={fc['confidence']}; probabilities={json.dumps(fc['probabilities'])}"),
        Feedback(name="failure_confidence", value=float(fc["confidence"])),
        Feedback(name="semantically_equivalent_p", value=float(a["semantically_equivalent"]["probability"])),
        Feedback(name="severity", value=float(sev["score"]), rationale=json.dumps(sev.get("legend"))),
    ]


# ---------- recommendation via Unity Gateway model service ----------
def gateway_chat(prompt: str) -> str:
    resp = w.api_client.do("POST", "/ai-gateway/mlflow/v1/chat/completions", body={
        "model": MODEL_SERVICE, "max_tokens": 200, "temperature": 0,
        "messages": [{"role": "user", "content": prompt}]})
    return resp["choices"][0]["message"]["content"].strip()


@scorer
def recommended_fix(inputs: dict, outputs: dict, expectations: dict) -> Feedback:
    if outputs.get("genie_sql") and compare(expectations["gt_rows"], outputs.get("rows") or []) == "match":
        return Feedback(name="recommended_fix", value="none")
    fix = gateway_chat(
        "You tune a Databricks Genie agent. Given a benchmark failure, propose ONE concise change "
        "(a text instruction, SQL expression/measure, or column description) that would fix this class of error "
        "for all similar questions. Max 2 sentences, no preamble.\n"
        f"Question: {inputs['question']}\nGround-truth SQL: {expectations['gt_sql']}\n"
        f"Genie SQL: {outputs.get('genie_sql') or '(none)'}\nGenie error: {outputs.get('error')}")
    return Feedback(name="recommended_fix", value=fix)


# ---------- data ----------
def build_dataset(core_ids: list[str] | None, limit: int | None) -> list[dict]:
    where = f"AND v.core_id IN ({','.join(sql_str(c) for c in core_ids)})" if core_ids else ""
    rows = sql(f"""SELECT v.test_id, v.core_id, v.question, v.arguments, c.gt_sql
                   FROM {SCHEMA}.variations v JOIN {SCHEMA}.core_questions c USING (core_id)
                   WHERE v.status = 'valid' {where} ORDER BY v.test_id""")
    rows = rows[:limit] if limit else rows
    gt_cache = {}  # ground truth executes ONCE per core_id, not per variation
    data = []
    for r in rows:
        if r["core_id"] not in gt_cache:
            res = w.statement_execution.execute_statement(warehouse_id=WAREHOUSE_ID, statement=r["gt_sql"], wait_timeout="50s")
            gt_cache[r["core_id"]] = (res.result.data_array or []) if res.result else []
        q = r["question"] + (f" (context: {r['arguments']})" if r["arguments"] else "")
        data.append({"inputs": {"question": q, "test_id": r["test_id"], "core_id": r["core_id"]},
                     "expectations": {"gt_sql": r["gt_sql"], "gt_rows": gt_cache[r["core_id"]]}})
    return data


def persist(run_id: str) -> None:
    sql(f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.harness_results (
              run_id STRING, run_ts TIMESTAMP, test_id STRING, core_id STRING, question STRING,
              result_match STRING, passed BOOLEAN, failure_category STRING, failure_confidence DOUBLE,
              semantically_equivalent_p DOUBLE, severity DOUBLE, recommended_fix STRING,
              genie_sql STRING, latency_s DOUBLE, trace_id STRING)""")
    traces = mlflow.search_traces(run_id=run_id)
    num = lambda v: "NULL" if v is None else str(float(v))
    vals = []
    for _, t in traces.iterrows():
        inp, out = t["request"] or {}, t["response"] or {}
        fb = {a["assessment_name"]: (a.get("feedback") or {}).get("value") for a in (t["assessments"] or [])}
        vals.append("(" + ",".join([
            sql_str(run_id), "current_timestamp()", sql_str(inp.get("test_id")), sql_str(inp.get("core_id")),
            sql_str(inp.get("question")), sql_str(fb.get("result_match")),
            "true" if fb.get("passed") in (True, "True", "true") else "false",
            sql_str(fb.get("failure_category")), num(fb.get("failure_confidence")),
            num(fb.get("semantically_equivalent_p")), num(fb.get("severity")), sql_str(fb.get("recommended_fix")),
            sql_str(out.get("genie_sql")), num(out.get("latency_s")), sql_str(t["trace_id"])]) + ")")
    if vals:
        sql(f"INSERT INTO {SCHEMA}.harness_results VALUES {','.join(vals)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core-ids", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", default="4")
    ap.add_argument("--run-name", default="genie-eval-harness")
    args = ap.parse_args()
    os.environ["MLFLOW_GENAI_EVAL_MAX_WORKERS"] = args.workers
    mlflow.set_tracking_uri(f"databricks://{PROFILE}")
    mlflow.set_experiment(EXPERIMENT)

    data = build_dataset(args.core_ids, args.limit)
    print(f"{len(data)} questions, workers={args.workers}")
    t0 = time.time()
    with mlflow.start_run(run_name=args.run_name) as run:
        res = mlflow.genai.evaluate(data=data, predict_fn=predict_fn,
                                    scorers=[result_match, failure_triage, recommended_fix])
        elapsed = time.time() - t0
        persist(run.info.run_id)
        # Core-question health logged as MLflow metrics as well
        per_core = sql(f"""SELECT core_id, round(avg(CASE WHEN passed THEN 1 ELSE 0 END), 3) AS pass_rate, count(*) n
                           FROM {SCHEMA}.harness_results WHERE run_id = {sql_str(run.info.run_id)} GROUP BY core_id""")
        mlflow.log_metrics({f"core_{r['core_id']}_pass_rate": float(r["pass_rate"]) for r in per_core})
        mlflow.log_metrics({"questions": len(data), "elapsed_s": round(elapsed),
                            "questions_per_min": round(len(data) / (elapsed / 60), 2)})
    print(f"run_id={run.info.run_id} elapsed={elapsed:.0f}s qpm={len(data)/(elapsed/60):.2f}")
    for r in sorted(per_core, key=lambda x: x["core_id"]):
        print(r)


if __name__ == "__main__":
    main()
