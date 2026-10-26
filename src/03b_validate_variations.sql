CREATE OR REPLACE TABLE {{schema}}.variations
COMMENT 'Benchmark variations validated with ai_decide(). Ground truth is inherited from core_questions via core_id.'
AS WITH src AS (
  SELECT c.core_id, c.question AS core_question, c.arguments, c.gt_sql, 'original' AS category, c.question AS question
  FROM {{schema}}.core_questions c
  UNION ALL
  SELECT v.core_id, c.question, c.arguments, c.gt_sql, v.category, v.question
  FROM {{schema}}.variations_raw v
  JOIN {{schema}}.core_questions c USING (core_id)
),
decided AS (
  SELECT *,
    ai_decide(
      to_json(named_struct('core_question', core_question, 'runtime_arguments', coalesce(arguments, 'none'),
                           'ground_truth_sql', gt_sql, 'candidate_variation', question)),
      '{
        "equivalent": {"type": "noul",
          "instructions": "Is the ground-truth SQL a fully correct answer to the candidate variation, i.e. the variation asks for the same metric, the same filters and filter values, the same time period and the same grouping? Treat typos, abbreviations, informal phrasing and equivalent date ranges (e.g. April through June 2026 = 2026-Q2) as equivalent. Pronouns like I/me/my refer to the runtime argument sales_rep_id."},
        "ambiguous": {"type": "noul",
          "instructions": "Is the candidate variation ambiguous enough that a competent analyst could reasonably write materially different SQL for it?"},
        "category": {"type": "choice",
          "instructions": "Which variation style best describes the candidate relative to the core question?",
          "criteria": {"original": "Identical to the core question",
                       "paraphrase": "Reworded with the same meaning",
                       "conversational": "Informal, chatty phrasing",
                       "contextual_pronoun": "Uses I/me/my/our to refer to the user context",
                       "time_variation": "Refers to the same time period in a different way",
                       "shorthand_typo": "Contains typos, abbreviations or poor grammar",
                       "edge_case_short": "Very short or terse keyword-style phrasing",
                       "filter_sensitivity": "Changes one filter value so the meaning differs",
                       "adversarial": "Ambiguous, multi-part or tricky phrasing"}}
      }'
    ) AS d
  FROM src
)
SELECT
  concat(core_id, '-', lpad(cast(row_number() OVER (PARTITION BY core_id ORDER BY category = 'original' DESC, category) - 1 AS STRING), 2, '0')) AS test_id,
  core_id, question, arguments, category AS generated_category,
  d:response.answers.category.choice::STRING            AS decided_category,
  round(d:response.answers.equivalent.probability::DOUBLE, 3) AS p_equivalent,
  round(d:response.answers.ambiguous.probability::DOUBLE, 3)  AS p_ambiguous,
  round(d:response.answers.category.confidence::DOUBLE, 3)    AS category_confidence,
  CASE WHEN d:error_message IS NOT NULL AND d:error_message::STRING IS NOT NULL THEN 'error'
       WHEN d:response.answers.equivalent.probability::DOUBLE >= 0.8 AND d:response.answers.ambiguous.probability::DOUBLE < 0.5 THEN 'valid'
       WHEN d:response.answers.equivalent.probability::DOUBLE < 0.3 THEN 'invalid'
       ELSE 'needs_review' END AS status,
  d:error_message::STRING AS decide_error
FROM decided
