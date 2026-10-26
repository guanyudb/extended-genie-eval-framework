CREATE OR REPLACE TABLE {{schema}}.native_recommendations
COMMENT 'One recommended Genie fix per core_id x failure_category (LLM via ai_query on a system.ai model)'
AS WITH patterns AS (
  SELECT core_id, failure_category, count(*) AS n_failures,
         first(question) AS example_question, first(genie_sql) AS example_genie_sql, first(gt_sql) AS gt_sql
  FROM {{schema}}.native_diagnostics
  WHERE triage_route <> 'likely_false_negative'
  GROUP BY core_id, failure_category
)
SELECT *, ai_query('{{model}}',
  concat('You tune a Databricks Genie agent. Propose ONE concise change (a text instruction, SQL measure/expression, ',
         'or column description) that fixes this failure pattern for all similar questions. Max 2 sentences, no preamble.',
         '\nFailure category: ', failure_category, '\nExample question: ', example_question,
         '\nGround-truth SQL: ', gt_sql, '\nGenie SQL: ', coalesce(example_genie_sql, '(none)')),
  failOnError => false).result AS recommended_fix
FROM patterns
