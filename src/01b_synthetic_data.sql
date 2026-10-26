CREATE OR REPLACE TABLE {{schema}}.rx_weekly
COMMENT 'Synthetic weekly prescription volumes by brand and territory. trx = total prescriptions, nrx = new prescriptions, market_trx = total TRx of the therapeutic market in the territory'
AS WITH base AS (
  SELECT w.week_end_date, b.brand, b.base, r.territory_id, r.sales_rep_id, r.region,
         abs(hash(w.week_end_date, b.brand, r.territory_id)) % 1000 AS h
  FROM (SELECT explode(sequence(DATE'2025-01-05', DATE'2026-09-27', INTERVAL 1 WEEK)) AS week_end_date) w
  CROSS JOIN (SELECT * FROM VALUES ('ZORVELA', 420), ('TELMARIX', 300), ('VYLENTRA', 250), ('KESTRAVO', 60) AS t(brand, base)) b
  CROSS JOIN {{schema}}.rep_territory r
)
SELECT week_end_date,
       concat(cast(year(week_end_date) AS STRING), '-Q', cast(quarter(week_end_date) AS STRING)) AS quarter,
       brand, territory_id, sales_rep_id, region,
       cast(base * (0.7 + h / 1500.0) * (1 + 0.004 * datediff(week_end_date, DATE'2025-01-05') / 7) AS INT) AS trx,
       cast(base * (0.7 + h / 1500.0) * 0.28 AS INT) AS nrx,
       cast(base * (0.7 + h / 1500.0) * (2.6 + (h % 7) / 10.0) AS INT) AS market_trx
FROM base
