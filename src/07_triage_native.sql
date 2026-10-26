CREATE OR REPLACE TABLE {{schema}}.native_diagnostics
COMMENT 'ai_decide() triage of non-GOOD native benchmark results: failure category, semantic equivalence, severity with confidences'
AS WITH latest AS (
  SELECT eval_run_id FROM {{schema}}.native_eval_results
  WHERE scope = 'full' ORDER BY run_ts DESC LIMIT 1
),
failed AS (
  SELECT n.*, v.question
  FROM {{schema}}.native_eval_results n
  JOIN latest USING (eval_run_id)
  JOIN {{schema}}.variations v USING (test_id)
  WHERE n.assessment <> 'GOOD'
),
decided AS (
  SELECT *, ai_decide(
    to_json(named_struct('question', question, 'ground_truth_sql', gt_sql, 'genie_sql', coalesce(genie_sql, '(none)'),
                         'native_assessment', assessment, 'native_assessment_reasons', assessment_reasons)),
    '{
      "semantically_equivalent": {"type": "noul", "instructions": "Would a business user get the same answer from the Genie SQL as from the ground-truth SQL, ignoring column names, column order, row order and rounding?"},
      "failure_category": {"type": "choice", "instructions": "Compare the Genie SQL with the ground-truth SQL. What is the primary reason they differ?",
        "criteria": {
          "missing_or_wrong_filter": "A required filter is missing, or a filter column/value is wrong",
          "wrong_aggregation": "Wrong aggregation function, or aggregates at the wrong level",
          "wrong_join": "Missing, extra or incorrect join",
          "wrong_metric_definition": "Computes a different business metric or formula than the ground truth (e.g. wrong ratio or scale)",
          "time_logic": "Wrong time period, date range, or period-comparison logic",
          "granularity_only": "Same numbers but different grouping, extra/missing columns, ordering or LIMIT only",
          "wrong_table_or_column": "Uses the wrong table or column",
          "misinterpretation": "Misunderstood the intent of the question",
          "execution_error": "Genie SQL failed or returned no result"}},
      "severity": {"type": "score", "instructions": "How harmful is this difference for a business user?",
        "criteria": ["Cosmetic: answer still usable", "Partially wrong: usable only with caveats", "Wrong: misleading answer"]}
    }') AS d
  FROM failed
)
SELECT eval_run_id, test_id, core_id, question, assessment, assessment_reasons, genie_sql, gt_sql,
       d:response.answers.failure_category.choice::STRING                AS failure_category,
       round(d:response.answers.failure_category.confidence::DOUBLE, 3)  AS failure_confidence,
       d:response.answers.failure_category.probabilities                 AS failure_probabilities,
       round(d:response.answers.semantically_equivalent.probability::DOUBLE, 3) AS p_semantically_equivalent,
       d:response.answers.severity.score::DOUBLE                         AS severity,
       round(d:response.answers.severity.confidence::DOUBLE, 3)          AS severity_confidence,
       CASE WHEN d:response.answers.semantically_equivalent.probability::DOUBLE >= 0.8 THEN 'likely_false_negative'
            WHEN d:response.answers.failure_category.confidence::DOUBLE < 0.6 THEN 'human_review'
            ELSE 'auto_triaged' END                                     AS triage_route,
       d:error_message::STRING AS decide_error
FROM decided
