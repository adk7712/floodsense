-- FloodSense dashboard tiles: live scorecard against PUB flash-flood alerts.
-- Source: workspace.floodsense.pub_scorecard (written by databricks/jobs/pub_scorecard.py).
-- LIVE MONITORING, not an accuracy result: few alerts. Keep the caption under the tile.
-- Tile caption: "Live monitoring against PUB alerts since 6 Oct 2026. Few alerts, rescored with the
-- current champion model; not an accuracy result."
-- Times are Singapore time. Add these as datasets in the existing dashboard by hand (the
-- .lvdash.json is not edited by this change).

-- Tile 1: one row per alert, newest first.
SELECT
  date_format(from_utc_timestamp(issued_at, 'Asia/Singapore'), 'dd MMM HH:mm') AS issued_sgt,
  initcap(zone)                                   AS planning_area,
  coalesce(max_tier, 'No score data')             AS highest_tier_in_prior_hour,
  round(max_probability * 100, 2)                 AS highest_probability_pct,
  CASE WHEN moderate_or_higher THEN round(warning_minutes) END AS minutes_of_warning,
  model_version
FROM workspace.floodsense.pub_scorecard
ORDER BY issued_at DESC
LIMIT 50;

-- Tile 2: counts and the 90% interval (never a bare rate). Returns no row until the job has run.
SELECT
  n_alerts                                        AS pub_alerts,
  n_scored                                        AS alerts_with_a_score,
  n_moderate_or_higher                            AS flagged_moderate_or_higher_beforehand,
  concat(n_moderate_or_higher, ' of ', n_scored, ' (90% interval ',
         round(rate_ci90_low * 100), '% to ', round(rate_ci90_high * 100), '%)') AS summary,
  caveat
FROM workspace.floodsense.pub_scorecard
LIMIT 1;
