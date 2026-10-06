"""
FloodSense - PUB Flood Prone Areas (annual hectares, 2022-2025), a Track B2 dataset.

Source: data.gov.sg dataset 'Flood Prone Areas' (PUB), dataset ID d_c4aed98f1533eb3a66f65dbb1a30da46.
Target: data/reference/pub_flood_prone_areas.csv, written by

    uv run python -m floodsense.data.flood_prone

The file is the dataset as published (year, hectares) plus where it came from. Nothing is typed in by
hand, and nothing is filled in when the file is missing: loading then fails.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests

from floodsense.common.config import settings

logger = logging.getLogger("FloodSense.FloodProne")

DATASET_ID = "d_c4aed98f1533eb3a66f65dbb1a30da46"
SOURCE_NAME = "PUB, Flood Prone Areas (data.gov.sg)"
SOURCE_URL = f"https://data.gov.sg/datasets?resultId={DATASET_ID}"
DATASTORE_URL = f"https://data.gov.sg/api/action/datastore_search?resource_id={DATASET_ID}"
FLOOD_PRONE_FILE = settings.root_dir / "data" / "reference" / "pub_flood_prone_areas.csv"


def download_flood_prone_areas(out_path: Path = FLOOD_PRONE_FILE) -> Path:
    """Fetch the dataset from data.gov.sg and write it, with its source, to ``out_path``."""
    resp = requests.get(DATASTORE_URL, timeout=15, headers={"User-Agent": "floodsense"})
    resp.raise_for_status()
    records = resp.json()["result"]["records"]
    if not records:
        raise RuntimeError(f"{DATASET_ID} returned no records")
    df = pd.DataFrame(
        {
            "year": [int(r["Year"]) for r in records],
            "flood_prone_hectares": [float(r["Hectares"]) for r in records],
        }
    ).sort_values("year")
    df["source_name"] = SOURCE_NAME
    df["source_url"] = SOURCE_URL
    df["retrieved_at"] = datetime.now(UTC).date().isoformat()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    logger.info("Wrote %d years of flood-prone hectares to %s", len(df), out_path)
    return out_path


def load_flood_prone_areas(path: Path | None = None) -> pd.DataFrame:
    """The published hectares per year. Raises ``FileNotFoundError`` when the file is missing."""
    target = path or FLOOD_PRONE_FILE
    if not target.exists():
        raise FileNotFoundError(
            f"{target} is missing; run `python -m floodsense.data.flood_prone` to download it"
        )
    return pd.read_csv(target).sort_values("year").reset_index(drop=True)


def build_trend_chart(df: pd.DataFrame | None = None) -> go.Figure:
    """Bar chart of flood-prone hectares per year, labelled with the source."""
    df = load_flood_prone_areas() if df is None else df
    years = [str(y) for y in df["year"]]
    fig = go.Figure(
        go.Bar(
            x=years,
            y=df["flood_prone_hectares"],
            marker_color="#0284C7",
            text=[f"{h:g} ha" for h in df["flood_prone_hectares"]],
            textposition="outside",
            hovertemplate="%{x}: %{y} ha<extra></extra>",
        )
    )
    fig.update_layout(
        title=dict(
            text=f"Flood-prone land, {years[0]}–{years[-1]} (source: {SOURCE_NAME})",
            font=dict(family="Inter, sans-serif", size=13, color="#0F172A"),
        ),
        yaxis=dict(
            title="Hectares",
            range=[0, float(df["flood_prone_hectares"].max()) * 1.25],
            gridcolor="#E2E8F0",
        ),
        xaxis=dict(showgrid=False),
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
        margin=dict(l=45, r=20, t=45, b=30),
        height=240,
        showlegend=False,
    )
    return fig


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    print(download_flood_prone_areas())
