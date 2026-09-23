-- Example analyses over the GA4 obfuscated ecommerce sample.
-- Each block starts with "-- name:" and "-- question:" headers so the file can be
-- parsed, validated (scripts/validate_examples.py) and reused as few-shot examples.
--
-- Conventions used throughout:
--   * Always filter on _TABLE_SUFFIX (YYYYMMDD) to limit bytes scanned.
--   * A session is (user_pseudo_id, ga_session_id); ga_session_id alone is not unique.
--   * Revenue uses ecommerce.purchase_revenue_in_usd / items.item_revenue_in_usd
--     (the non-USD columns are NULL on ~8% of purchases).
--   * Session-level channel comes from event_params source/medium/campaign;
--     traffic_source.* is the user's FIRST-touch acquisition channel.


-- name: daily_kpis
-- question: How did users, sessions, purchases and revenue trend day by day?
SELECT
  PARSE_DATE('%Y%m%d', event_date) AS day,
  COUNT(DISTINCT user_pseudo_id) AS users,
  COUNT(DISTINCT CONCAT(user_pseudo_id, CAST(
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS STRING))) AS sessions,
  COUNTIF(event_name = 'purchase') AS purchases,
  ROUND(SUM(IF(event_name = 'purchase', ecommerce.purchase_revenue_in_usd, 0)), 2) AS revenue_usd
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
GROUP BY day
ORDER BY day;


-- name: purchase_funnel
-- question: What does the purchase funnel look like and where do users drop off?
WITH steps AS (
  SELECT
    COUNT(DISTINCT IF(event_name = 'view_item', user_pseudo_id, NULL)) AS view_item,
    COUNT(DISTINCT IF(event_name = 'add_to_cart', user_pseudo_id, NULL)) AS add_to_cart,
    COUNT(DISTINCT IF(event_name = 'begin_checkout', user_pseudo_id, NULL)) AS begin_checkout,
    COUNT(DISTINCT IF(event_name = 'add_shipping_info', user_pseudo_id, NULL)) AS add_shipping_info,
    COUNT(DISTINCT IF(event_name = 'add_payment_info', user_pseudo_id, NULL)) AS add_payment_info,
    COUNT(DISTINCT IF(event_name = 'purchase', user_pseudo_id, NULL)) AS purchase
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
)
SELECT step, users, ROUND(users / FIRST_VALUE(users) OVER (ORDER BY step_order), 4) AS pct_of_first_step
FROM steps
UNPIVOT (users FOR step IN (view_item, add_to_cart, begin_checkout, add_shipping_info, add_payment_info, purchase))
JOIN UNNEST([
  STRUCT('view_item' AS s, 1 AS step_order), ('add_to_cart', 2), ('begin_checkout', 3),
  ('add_shipping_info', 4), ('add_payment_info', 5), ('purchase', 6)
]) ON s = step
ORDER BY step_order;


-- name: channel_performance
-- question: Which marketing channels drive sessions, conversions and revenue?
WITH sessions AS (
  SELECT
    CONCAT(user_pseudo_id, CAST(
      (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS STRING)) AS session_id,
    -- The session's source/medium is on its session_start / first events; take any non-null value.
    ANY_VALUE((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'source')) AS source,
    ANY_VALUE((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'medium')) AS medium,
    COUNTIF(event_name = 'purchase') > 0 AS converted,
    SUM(IF(event_name = 'purchase', ecommerce.purchase_revenue_in_usd, 0)) AS revenue
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201201' AND '20201231'
  GROUP BY session_id
)
SELECT
  -- ~20% of sessions carry no source/medium params at all; label them explicitly.
  CONCAT(IFNULL(source, '(not set)'), ' / ', IFNULL(medium, '(not set)')) AS source_medium,
  COUNT(*) AS sessions,
  COUNTIF(converted) AS converting_sessions,
  ROUND(COUNTIF(converted) / COUNT(*), 4) AS conversion_rate,
  ROUND(SUM(revenue), 2) AS revenue_usd
FROM sessions
GROUP BY source_medium
ORDER BY sessions DESC
LIMIT 15;


-- name: top_products_by_revenue
-- question: Which products generate the most revenue?
SELECT
  i.item_name,
  i.item_category,
  SUM(i.quantity) AS units_sold,
  ROUND(SUM(i.item_revenue_in_usd), 2) AS revenue_usd,
  ROUND(SAFE_DIVIDE(SUM(i.item_revenue_in_usd), SUM(i.quantity)), 2) AS avg_unit_price
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, UNNEST(items) AS i
WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
  AND event_name = 'purchase'
  AND i.item_name != '(not set)'
GROUP BY i.item_name, i.item_category
ORDER BY revenue_usd DESC
LIMIT 20;


-- name: product_conversion
-- question: For the most viewed products, how often do views turn into carts and purchases?
-- Grouped by item_name, not item_category: categories are paths ('Home/Apparel/') on
-- view/cart events but flat ('Apparel') on purchases, so they don't line up across steps.
SELECT
  i.item_name,
  COUNTIF(event_name = 'view_item') AS item_views,
  COUNTIF(event_name = 'add_to_cart') AS add_to_carts,
  COUNTIF(event_name = 'purchase') AS item_purchases,
  ROUND(SAFE_DIVIDE(COUNTIF(event_name = 'add_to_cart'), COUNTIF(event_name = 'view_item')), 4) AS view_to_cart,
  ROUND(SAFE_DIVIDE(COUNTIF(event_name = 'purchase'), COUNTIF(event_name = 'add_to_cart')), 4) AS cart_to_purchase
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, UNNEST(items) AS i
WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
  AND event_name IN ('view_item', 'add_to_cart', 'purchase')
  AND i.item_name != '(not set)'
GROUP BY i.item_name
ORDER BY item_views DESC
LIMIT 20;


-- name: device_performance
-- question: How do desktop, mobile and tablet compare on conversion and order value?
SELECT
  device.category AS device,
  COUNT(DISTINCT user_pseudo_id) AS users,
  COUNT(DISTINCT IF(event_name = 'purchase', user_pseudo_id, NULL)) AS purchasers,
  ROUND(SAFE_DIVIDE(COUNT(DISTINCT IF(event_name = 'purchase', user_pseudo_id, NULL)),
                    COUNT(DISTINCT user_pseudo_id)), 4) AS user_conversion_rate,
  ROUND(SUM(IF(event_name = 'purchase', ecommerce.purchase_revenue_in_usd, 0)), 2) AS revenue_usd,
  ROUND(AVG(IF(event_name = 'purchase', ecommerce.purchase_revenue_in_usd, NULL)), 2) AS avg_order_value
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
GROUP BY device
ORDER BY users DESC;


-- name: revenue_by_country
-- question: Which countries bring the most users and revenue?
SELECT
  geo.country,
  COUNT(DISTINCT user_pseudo_id) AS users,
  COUNTIF(event_name = 'purchase') AS purchases,
  ROUND(SUM(IF(event_name = 'purchase', ecommerce.purchase_revenue_in_usd, 0)), 2) AS revenue_usd
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
GROUP BY geo.country
ORDER BY revenue_usd DESC
LIMIT 15;


-- name: new_vs_returning
-- question: How do new and returning visitors differ in engagement and conversion?
WITH sessions AS (
  SELECT
    user_pseudo_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
    MAX((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_number')) AS session_number,
    COUNTIF(event_name = 'page_view') AS pageviews,
    COUNTIF(event_name = 'purchase') > 0 AS converted
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
  GROUP BY user_pseudo_id, session_id
)
SELECT
  IF(session_number = 1, 'new', 'returning') AS visitor_type,
  COUNT(*) AS sessions,
  ROUND(AVG(pageviews), 2) AS avg_pageviews,
  ROUND(COUNTIF(converted) / COUNT(*), 4) AS session_conversion_rate
FROM sessions
WHERE session_number IS NOT NULL
GROUP BY visitor_type;


-- name: top_landing_pages
-- question: Which landing pages start the most sessions, and how well do they convert?
WITH landings AS (
  SELECT
    user_pseudo_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
    REGEXP_REPLACE(
      (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location'),
      r'\?.*$', '') AS landing_page
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
    AND event_name = 'page_view'
    AND (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'entrances') = 1
),
purchases AS (
  SELECT DISTINCT
    user_pseudo_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
    AND event_name = 'purchase'
)
SELECT
  l.landing_page,
  COUNT(*) AS entrances,
  COUNT(p.user_pseudo_id) AS converting_sessions,
  ROUND(COUNT(p.user_pseudo_id) / COUNT(*), 4) AS conversion_rate
FROM landings AS l
LEFT JOIN purchases AS p USING (user_pseudo_id, session_id)
GROUP BY l.landing_page
ORDER BY entrances DESC
LIMIT 15;


-- name: activity_by_weekday_hour
-- question: At what days and hours are users most active and most likely to buy?
SELECT
  FORMAT_TIMESTAMP('%A', TIMESTAMP_MICROS(event_timestamp)) AS weekday,
  EXTRACT(DAYOFWEEK FROM TIMESTAMP_MICROS(event_timestamp)) AS weekday_num,
  EXTRACT(HOUR FROM TIMESTAMP_MICROS(event_timestamp)) AS hour_utc,
  COUNTIF(event_name = 'page_view') AS pageviews,
  COUNTIF(event_name = 'purchase') AS purchases
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
GROUP BY weekday, weekday_num, hour_utc
ORDER BY weekday_num, hour_utc;


-- name: promotion_performance
-- question: Which on-site promotions get clicked most after being seen?
SELECT
  i.promotion_name,
  COUNTIF(event_name = 'view_promotion') AS views,
  COUNTIF(event_name = 'select_promotion') AS clicks,
  ROUND(SAFE_DIVIDE(COUNTIF(event_name = 'select_promotion'), COUNTIF(event_name = 'view_promotion')), 4) AS click_through_rate
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, UNNEST(items) AS i
WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
  AND event_name IN ('view_promotion', 'select_promotion')
  AND i.promotion_name NOT IN ('', '(not set)')
GROUP BY i.promotion_name
ORDER BY views DESC;


-- name: weekly_retention_cohorts
-- question: Of users first seen in a given week, what share come back in later weeks?
WITH first_seen AS (
  SELECT user_pseudo_id, DATE_TRUNC(DATE(TIMESTAMP_MICROS(MIN(user_first_touch_timestamp))), WEEK) AS cohort_week
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
  GROUP BY user_pseudo_id
),
activity AS (
  SELECT DISTINCT user_pseudo_id, DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), WEEK) AS active_week
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
)
SELECT
  f.cohort_week,
  DATE_DIFF(a.active_week, f.cohort_week, WEEK) AS weeks_since_first_visit,
  COUNT(DISTINCT a.user_pseudo_id) AS active_users,
  ROUND(COUNT(DISTINCT a.user_pseudo_id) / MAX(c.cohort_size), 4) AS retention
FROM first_seen AS f
JOIN activity AS a USING (user_pseudo_id)
JOIN (SELECT cohort_week, COUNT(*) AS cohort_size FROM first_seen GROUP BY cohort_week) AS c USING (cohort_week)
WHERE f.cohort_week >= '2020-11-01' AND a.active_week >= f.cohort_week
GROUP BY f.cohort_week, weeks_since_first_visit
HAVING weeks_since_first_visit <= 8
ORDER BY f.cohort_week, weeks_since_first_visit;
