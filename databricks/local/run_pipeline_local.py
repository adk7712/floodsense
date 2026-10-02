"""Run databricks/pipelines/lakeflow_pipeline.py in local Spark on the 17 Apr 2021 replay.

See DEPLOYMENT.md section 9. Needs PySpark and Java 17+; not part of CI.
"""

import importlib.util, os, sys, tempfile, time
import pandas as pd
from pyspark.sql import SparkSession, functions as F

S = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, S)  # the dlt stand-in next to this file
import dlt  # noqa: E402

from floodsense.serving import pipeline_core as core  # noqa: E402
from floodsense.common.config import settings  # noqa: E402
from floodsense.data.replay import load_replay  # noqa: E402

tz = sys.argv[1] if len(sys.argv) > 1 else "UTC"
spark = (SparkSession.builder.master("local[4]").config("spark.ui.enabled", "false")
         .config("spark.sql.session.timeZone", tz).config("spark.sql.shuffle.partitions", "4").getOrCreate())
spark.sparkContext.setLogLevel("ERROR")

landing = tempfile.mkdtemp()
files = core.export_replay_payloads(__import__("pathlib").Path(landing))
with open(os.path.join(landing, "rainfall_broken.json"), "w") as f:
    f.write('{"not": "a rainfall payload"')  # malformed: must be quarantined, not crash

spec = importlib.util.spec_from_file_location("lf", os.path.join(S, "..", "pipelines", "lakeflow_pipeline.py"))
lf = importlib.util.module_from_spec(spec)
lf.spark = spark
spec.loader.exec_module(lf)

def bronze():
    return (spark.read.text(landing, wholetext=True)
            .select(F.col("value").alias("raw_payload_text"),
                    F.col("_metadata.file_name").alias("source_file"),
                    F.col("_metadata.file_modification_time").alias("ingested_at")))

t = time.time()
res = dlt.run({"raw_rainfall_bronze": bronze})
counts = {n: res[n].count() for n in res}
print("session tz", tz, "| rows", counts, "| %.0fs" % (time.time() - t))
print("quarantine:", [r.quarantine_reason[:60] for r in res["raw_payloads_quarantine"].collect()])

gold = res["flood_risk_predictions_gold"].toPandas()
gold = core.spark_to_sgt(gold, tz)
replay = load_replay(settings.replay_file)
win = (gold["timestamp"] >= replay.display_start) & (gold["timestamp"] <= replay.display_end)
got = gold[win].sort_values(["timestamp", "ura_planning_area"]).reset_index(drop=True)
ref = core.replay_predictions()
same_rows = got[["ura_planning_area", "timestamp"]].equals(ref[["ura_planning_area", "timestamp"]])
same_tiers = (got["risk_tier"].values == ref["risk_tier"].values).all() if same_rows else False
maxdiff = float((got["flood_probability"] - ref["flood_probability"]).abs().max()) if same_rows else None
bt = got[(got.ura_planning_area == "BUKIT TIMAH") & (got.risk_tier == "High")]["timestamp"].min()
print("rows", len(got), "vs", len(ref), "| same rows", same_rows, "| same tiers", same_tiers,
      "| max prob diff", maxdiff, "| Bukit Timah first High", bt)
