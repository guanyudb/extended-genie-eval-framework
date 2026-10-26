CREATE OR REPLACE TABLE {{schema}}.classifier_comparison AS
WITH base AS (
  SELECT d.*,
    CASE
      WHEN assessment_reasons LIKE '%EMPTY_RESULT%' THEN 'execution_error'
      WHEN assessment_reasons LIKE '%INCORRECT_METRIC_CALCULATION%' THEN 'wrong_metric_definition'
      WHEN assessment_reasons LIKE '%MISSING_OR_INCORRECT_JOIN%' THEN 'wrong_join'
      WHEN assessment_reasons LIKE '%MISSING_OR_INCORRECT_FILTER%' THEN 'missing_or_wrong_filter'
      WHEN assessment_reasons LIKE '%INCOMPLETE_OR_PARTIAL_OUTPUT%' OR assessment_reasons LIKE '%FORMATTING_ERROR%' THEN 'granularity_only'
      ELSE 'other' END AS native_category
  FROM {{schema}}.native_diagnostics d
)
SELECT test_id, core_id, native_category, failure_category AS ai_decide_category, failure_confidence,
  lower(trim(regexp_extract(ai_query('{{model}}',
    concat('Classify the primary reason the Genie SQL differs from the ground-truth SQL. Answer with exactly one label from: ',
           'missing_or_wrong_filter, wrong_aggregation, wrong_join, wrong_metric_definition, time_logic, granularity_only, ',
           'wrong_table_or_column, misinterpretation, execution_error.',
           '\nQuestion: ', question, '\nGround-truth SQL: ', gt_sql, '\nGenie SQL: ', coalesce(genie_sql, '(none)'),
           '\nNative assessment reasons: ', assessment_reasons),
    failOnError => false).result, '([a-z_]+)', 1))) AS llm_judge_category
FROM base
