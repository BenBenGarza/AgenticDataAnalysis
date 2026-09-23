# GA4 ecommerce sample: dataset notes

Findings from profiling `bigquery-public-data.ga4_obfuscated_sample_ecommerce`.
These notes are the source material for the agent's system prompt; validated
query patterns live in [example_queries.sql](example_queries.sql).

## Shape

- Google Merchandise Store web data, **2020-11-01 to 2021-01-31** (92 daily tables `events_YYYYMMDD`).
- **One row per event.** ~4.3M events, ~270K users, ~360K sessions, 5,692 purchases, ~$362K revenue.
- Single web stream (`platform = 'WEB'`); `app_info` is empty.

## Identifiers

| Concept | How to get it |
|---|---|
| User | `user_pseudo_id` (`user_id` is always NULL) |
| Session | `(user_pseudo_id, ga_session_id)` — `ga_session_id` alone is **not** unique |
| New vs returning | `ga_session_number = 1` means first session |
| Order | Count `purchase` events. `transaction_id` is `'(not set)'` on 883 purchases |

`ga_session_id`, `ga_session_number`, `page_location`, `entrances` etc. live in the
`event_params` array. Extract with a scalar subquery:

```sql
(SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id')
```

Useful `event_params` keys: `ga_session_id`, `ga_session_number` (int), `page_location`,
`page_title`, `page_referrer`, `source`, `medium`, `campaign`, `term` (string),
`entrances` (int, 1 on landing page view), `engagement_time_msec` (int),
`percent_scrolled` (int), `search_term` (always `<obfuscated>`).

## Events

Volume order: `page_view`, `user_engagement`, `scroll`, `view_item`, `session_start`,
`first_visit`, `view_promotion`, `add_to_cart`, `begin_checkout`, `select_item`,
`view_search_results`, `add_shipping_info`, `add_payment_info`, `select_promotion`, `purchase`.

Funnel: `view_item` → `add_to_cart` → `begin_checkout` → `add_shipping_info` → `add_payment_info` → `purchase`.

## Revenue and items

- Use **`ecommerce.purchase_revenue_in_usd`** — `purchase_revenue` is NULL on ~450 purchases.
- `items` array: one entry per product on ecommerce events. `item_revenue_in_usd` and
  `price_in_usd` are populated **only on `purchase`** events.
- `item_category` is **inconsistent across events**: a path on views/carts
  (`Home/Apparel/Men's / Unisex/`), a flat name on purchases (`Apparel`). Compare products
  across funnel steps by `item_name`, not category.
- `item_name = '(not set)'` appears on ~3% of purchase items; exclude from product rankings.

## Traffic attribution (common pitfall)

- `traffic_source.source/medium/name` = the user's **first-touch** acquisition channel,
  identical on every event of that user.
- Session-level channel = `source`/`medium`/`campaign` in `event_params`.
  ~20% of sessions have none — label as `(not set)`.
- Pick the one that matches the question ("how were users acquired" vs "which channel drove this visit").

## Obfuscation and data quality

The dataset is deliberately obfuscated. Narratives should call these out, not treat them as real values:

| Value | Where | Share |
|---|---|---|
| `<Other>` | source, medium, campaign, browser, OS, mobile brand | ~26% of events' first-touch source |
| `(data deleted)` | source, medium, campaign | ~7% of events' first-touch source |
| `(not set)` / empty | city (~42%), item names/categories, geo | varies |
| `<obfuscated>` | `term`, `search_term` | all values |
| `NULL` | `device.language` (~43%) | |

Other quirks:
- `device.operating_system = 'Web'` for ~58% of events (obfuscated desktop OS).
- Promotions can show more `select_promotion` than `view_promotion` (CTR > 100%) — tracking gap, not a real rate.
- All timestamps are **UTC** microseconds (`TIMESTAMP_MICROS(event_timestamp)`).

## Cost

- Whole dataset per column is small, but **`event_params` is the heavy column**: queries that
  unnest it across 3 months scan ~1–1.5 GB; queries on top-level fields scan 50–250 MB.
- Always filter `_TABLE_SUFFIX BETWEEN 'YYYYMMDD' AND 'YYYYMMDD'`; narrow date ranges when possible.
- Dry runs (`QueryJobConfig(dry_run=True)`) are free and return bytes scanned.
