CREATE OR REPLACE TABLE {{schema}}.variations_raw AS
WITH gen AS (
  SELECT core_id, arguments,
    ai_query(
      '{{model}}',
      concat(
        'You generate benchmark question variations to stress-test a text-to-SQL agent for a pharma commercial team. ',
        'Parse the ground-truth SQL to extract its semantic contract (metric, filters, time period, grouping). ',
        'Return ONLY a raw JSON object (no markdown) {"variations":[{"category":"...","question":"..."}]} with exactly one variation for EACH category: ',
        'paraphrase, conversational, contextual_pronoun, time_variation, shorthand_typo, edge_case_short, filter_sensitivity, adversarial. ',
        'Rules: all categories except filter_sensitivity and adversarial MUST keep the exact same semantics as the SQL. ',
        'time_variation must refer to the SAME period differently (e.g. "April through June 2026" for 2026-Q2). ',
        'contextual_pronoun uses I/me/my only if a sales_rep_id argument exists, otherwise refers to "our team". ',
        'filter_sensitivity changes exactly one filter value. adversarial is ambiguous or tricky phrasing. ',
        'Core question: ', question, '\nArguments: ', coalesce(arguments, 'none'), '\nGround-truth SQL: ', gt_sql),
      failOnError => false,
      modelParameters => named_struct('temperature', CAST(0.7 AS DOUBLE), 'max_tokens', 1500)
    ) AS r
  FROM {{schema}}.core_questions
)
SELECT core_id, arguments, v.category, v.question, r.errorMessage AS gen_error
FROM gen
LATERAL VIEW explode(from_json(regexp_extract(r.result, '(?s)\\{.*\\}', 0), 'STRUCT<variations: ARRAY<STRUCT<category: STRING, question: STRING>>>').variations) AS v
