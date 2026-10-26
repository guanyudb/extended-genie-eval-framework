CREATE OR REPLACE TABLE {{schema}}.rep_territory
COMMENT 'Synthetic sales rep to territory/region mapping'
AS SELECT
  concat('REP', lpad(cast(id AS STRING), 3, '0')) AS sales_rep_id,
  element_at(array('Kevin','Priya','Marcus','Elena','Tom','Aisha','Diego','Mei','Sam','Olivia',
                   'Raj','Nora','Liam','Zoe','Omar','Grace','Ivan','Hana','Leo','Maya'), cast(id AS INT)) AS rep_name,
  concat('T', lpad(cast(id AS STRING), 2, '0')) AS territory_id,
  CASE WHEN id <= 5 THEN 'Northeast' WHEN id <= 10 THEN 'Southeast' WHEN id <= 15 THEN 'Central' ELSE 'West' END AS region
FROM range(1, 21) AS t(id)
