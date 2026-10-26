CREATE OR REPLACE VIEW {{schema}}.core_health_current
COMMENT 'Current core-question health: latest result per test_id per source (full + selective re-runs), aggregated by core_id'
AS WITH unified AS (
  SELECT 'native_eval_run' AS source, eval_run_id AS run_id, run_ts, test_id, core_id, assessment = 'GOOD' AS passed
  FROM {{schema}}.native_eval_results WHERE test_id IS NOT NULL
  UNION ALL
  SELECT 'mlflow_harness', run_id, run_ts, test_id, core_id, passed
  FROM {{schema}}.harness_results
),
latest AS (
  SELECT * FROM unified QUALIFY ROW_NUMBER() OVER (PARTITION BY source, test_id ORDER BY run_ts DESC) = 1
)
SELECT l.source, l.core_id, concat(l.core_id, ' - ', c.question) AS core_label, max(l.run_ts) AS last_evaluated,
       count(*) AS variations, count_if(passed) AS pass, count_if(NOT passed) AS fail,
       round(avg(CASE WHEN passed THEN 1.0 ELSE 0.0 END), 3) AS pass_rate,
       CASE WHEN avg(CASE WHEN passed THEN 1.0 ELSE 0.0 END) >= 0.8 THEN 'Healthy' ELSE 'Needs attention' END AS health_status
FROM latest l JOIN {{schema}}.core_questions c USING (core_id)
GROUP BY l.source, l.core_id, c.question
