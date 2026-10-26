CREATE OR REPLACE TABLE {{schema}}.core_questions (
  core_id STRING NOT NULL COMMENT 'Core business question ID (e.g. Q001)',
  question STRING COMMENT 'Canonical natural-language question',
  arguments STRING COMMENT 'Runtime arguments, e.g. sales_rep_id',
  gt_sql STRING COMMENT 'Ground-truth SQL - single source of truth for all variations of this core question',
  complexity STRING,
  business_dimension STRING,
  updated_by STRING,
  update_reason STRING
) COMMENT 'Core Q&A set. Ground truth lives ONLY here; variations inherit it by core_id.'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
