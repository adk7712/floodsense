# Live scorecard against PUB flash-flood alerts

**Live monitoring, not an accuracy result.** It lists, for each PUB "flash flood occurred" alert,
what FloodSense showed for that planning area just before. There are few alerts, so any rate is
shown as raw counts with a 90% interval and should not be read as the model's hit rate.

Code: `src/floodsense/monitoring/pub_scorecard.py` (pure functions and a local command),
`databricks/jobs/pub_scorecard.py` and `pub_scorecard_job.json` (the Delta table job, **not deployed**),
`databricks/dashboards/pub_scorecard_tile.sql` (tile queries), `tests/test_pub_scorecard.py`.

## What it measures

For every PUB alert (message type "Alert", one per identifier, issued on or after 6 Oct 2026 SGT):

- **Planning area:** the area containing the alert's centre point, as the app's "PUB alert active
  here" box does. The areas the alert circle also touches are listed in `zones_touched` but not scored.
- **Highest tier and probability** FloodSense gave that area on any 5-minute step in the 60 minutes up
  to and including the alert time.
- **Minutes of warning:** alert time minus the first Moderate or High step in that hour. Empty if the
  area stayed Low.
- **Summary:** "N of M alerts had Moderate or High beforehand", with a 90% bootstrap interval from
  `models.evaluation.hit_rate_ci` (the interval method the repo uses for event rates).

Steps with no data ("No data") are never treated as Low. An alert at sea, or with no usable score in
the hour, is shown as `no_score_data` and left out of the count M.

## What "beforehand" means (and a limit)

`flood_risk_predictions_gold` is rewritten on each pipeline update and holds only the **last 24 hours**
(1 query on 22 Oct showed 15,840 rows, 55 areas x 288 steps, newest 18 Oct 03:40 SGT). It cannot show
what was displayed before an older alert, and nothing snapshots it. So the scorecard **rescores** the
kept rainfall history (`rainfall_readings_silver`) with the registered `champion` model for the hour
before each alert. This is what the *current* champion would have shown, using the same scoring code as
gold; it can differ from what the app displayed at the time if the champion has changed since
(version 3 from 18 Oct). The model version is stored on each row. Alerts older than the rainfall silver
history, or in a gap in gauge data, come out as `no_score_data`.

Also not measured: false alarms (nothing scores the hours with Moderate/High but no PUB alert), alerts
PUB did not publish.

**Test set (CLAUDE.md rule 2).** This uses live data from Oct 2026 onward, which is inside the
2024–2026 test window. It is never used to choose or tune the model. Any rate it shows is a
**"second look"**, not the tested result (agreed by both, 22 Oct; the same treatment as the radar
results, decision 4).

## Current numbers

**0 PUB alerts so far; the scorecard is empty.** Checked read-only on 22 Oct 2026:

- All 25 archived alert pages in `/Volumes/workspace/floodsense/landing/flood_alerts/` (6, 16 and 18 Oct
  files) hold 0 alert readings.
- `workspace.floodsense.pub_flood_alerts_silver` has 0 rows.
- `rainfall_readings_silver` ends at 18 Oct 03:45 SGT, so any alert after that could not be scored yet
  anyway.

No hit rate or interval exists. Run the job after alerts have been archived.

## How to run it

Locally, on API pages you have as files:

```bash
.venv/bin/python -m floodsense.monitoring.pub_scorecard \
  --alerts-dir <folder of flood-alert JSON pages> \
  --rainfall-dir <folder of rainfall JSON pages> --out scorecard.csv
```

The rainfall pages must start 72 hours before the first alert (wet-ground feature). Or pass
`--scores history.parquet` with the gold columns instead of `--rainfall-dir`.

## How to deploy (Akul, needs your OK; nothing here has been run on Databricks)

1. Rebuild and upload the project wheel so it includes `floodsense.monitoring`, as in DEPLOYMENT.md
   (it replaces `floodsense-0.1.0-py3-none-any.whl` in `/Volumes/workspace/floodsense/artifacts/wheels/`).
2. Upload the job script:
   `databricks workspace import /Workspace/Users/<login>/floodsense/pub_scorecard.py --file databricks/jobs/pub_scorecard.py --format AUTO --overwrite`
3. Open `databricks/jobs/pub_scorecard_job.json` and replace `<your-login>` with your login.
4. Create the job (it is created PAUSED):
   `databricks jobs create --json @databricks/jobs/pub_scorecard_job.json`
5. Run it once by hand: `databricks jobs run-now <job_id>`. Check the run log's last two lines, and
   `SELECT * FROM workspace.floodsense.pub_scorecard`. With no alerts the table is empty.
6. To add the tiles, copy the two queries in `databricks/dashboards/pub_scorecard_tile.sql` into the
   dashboard as datasets and add a tile for each, with the caption from that file under them.
7. When you are happy, unpause the job (daily 06:30 SGT). It uses serverless compute for a minute or so.

**Unverified:** the Databricks job and the tile SQL have not been run (no deploys were allowed). The
local functions are tested on synthetic data only.

## Follow-up worth considering (not built)

Add a small step that appends the newest gold rows to a history table, so later scorecards can show the
scores *as actually served* rather than rescored.
