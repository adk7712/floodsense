# FloodSense live dashboard (Databricks AI/BI)

Definition: `floodsense_live_risk.lvdash.json` (reads `workspace.floodsense.*`; times shown in SGT).

Recreate (profile `akul`, warehouse `dfa0305abf58b59e`):

1. Wrap the JSON file as a string and create the draft:
   `databricks lakeview create --json '{"display_name":"FloodSense – Live Flood Risk","warehouse_id":"dfa0305abf58b59e","parent_path":"/Users/<you>","serialized_dashboard":"<contents of the .lvdash.json as a JSON string>"}'`
2. Publish it:
   `databricks lakeview publish <dashboard_id> --json '{"warehouse_id":"dfa0305abf58b59e","embed_credentials":true}'`

Deployed id: 01f1c168d5ba1a44819705ab969bf3ea
