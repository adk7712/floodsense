"""
FloodSense - IDW vs ordinary kriging, leave-one-gauge-out (plan item B3).

Run: ``python -m floodsense.spatial.compare_interpolation``

Uses 2017-2023 only. 2024-2026 is the held-out test set and is refused in code.

Design
------
* Storm steps: 5-minute steps where at least one reporting gauge read >= ``--storm-mm`` (default
  2.0 mm in 5 min, about 24 mm/h). Steps are drawn at random (seeded) from all storm steps in
  2017-2023: ``--n-fit`` steps to fit the variogram, a disjoint ``--n-eval`` steps to validate.
* Each validation step: every gauge with a reading is predicted from the other reporting gauges
  by IDW (``IDWMatrixEngine``, p=2, weights rebalanced over reporting gauges, self excluded) and
  by ordinary kriging. Steps/gauges with fewer than ``MIN_NEIGHBOURS`` other readings are
  skipped for both methods. Missing readings are missing, never 0.
* Station coordinates are the ones valid at each step (some gauges moved).
* Gauges are grouped by observed intensity (5-min reading x 12, mm/h-equivalent): light < 10,
  heavy >= 10 (and a sub-row for very heavy >= 30). Errors are reported in mm/h-equivalent.
* 90% interval on the RMSE difference (kriging minus IDW): percentile bootstrap resampling time
  steps, ``--boot`` resamples. Because random steps from one storm are correlated, a second
  interval resampling whole calendar days is also given; the more cautious one is the one to trust.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.data.rainfall_store import SGT, load_station_table, read_rainfall, stations_at
from floodsense.spatial.idw_matrix import IDWMatrixEngine, haversine_distance_km
from floodsense.spatial.kriging import (
    OrdinaryKriger,
    SemivarianceBins,
    Variogram,
    fit_variogram,
)

MIN_NEIGHBOURS = 4
HEAVY_MMH = 10.0
VERY_HEAVY_MMH = 30.0
TO_MMH = 12.0  # 5-minute total -> mm/h-equivalent
FIRST_YEAR = 2017
DEFAULT_OUT = Path("docs/results/kriging_vs_idw.json")


def assert_training_window(start_year: int, end_year: int) -> None:
    """Refuse anything outside 2017..last_training_year (2024+ is the held-out test set)."""
    if end_year >= settings.test_start_year or end_year > settings.last_training_year:
        raise ValueError(
            f"{end_year} is in the held-out test set (>= {settings.test_start_year}); "
            f"this comparison may only use data up to {settings.last_training_year}"
        )
    if start_year > end_year:
        raise ValueError("start_year must not exceed end_year")


def _segments(first: int, last: int) -> list[pd.Timestamp]:
    """Timestamps at which the active station set or locations change, plus the window start."""
    table = load_station_table()
    start = pd.Timestamp(f"{first}-01-01", tz=SGT)
    end = pd.Timestamp(f"{last}-12-31 23:55", tz=SGT)
    changes = pd.concat([table["valid_from"], table["valid_to"]]).dropna()
    inside = sorted(set(changes[(changes > start) & (changes <= end)]))
    return [start, *inside]


def collect_storm_steps(first: int, last: int, storm_mm: float) -> tuple[pd.DataFrame, int]:
    """
    Wide frame (rows = storm steps, columns = station ids, NaN = no reading) for every 5-min step
    in ``first..last`` whose maximum reading is >= ``storm_mm``; plus total steps scanned.
    """
    assert_training_window(first, last)
    parts: list[pd.DataFrame] = []
    scanned = 0
    for year in range(first, last + 1):
        df = read_rainfall(
            pd.Timestamp(f"{year}-01-01", tz=SGT), pd.Timestamp(f"{year}-12-31 23:55", tz=SGT)
        )
        if (df["timestamp"].dt.year > settings.last_training_year).any():
            raise ValueError("store returned rows beyond the training window")
        wide = df.pivot(index="timestamp", columns="station_id", values="rainfall_mm")
        scanned += len(wide)
        parts.append(wide[wide.max(axis=1) >= storm_mm])
    return pd.concat(parts).sort_index(), scanned


class _Geometry:
    """Station coordinates, distances and IDW engines per station-location period."""

    def __init__(self, first: int, last: int):
        self.bounds = _segments(first, last)
        self._cache: dict[int, tuple[list[str], np.ndarray, IDWMatrixEngine]] = {}

    def segment_of(self, ts: pd.Timestamp) -> int:
        return max(int(np.searchsorted(self.bounds, ts, side="right")) - 1, 0)

    def get(self, seg: int) -> tuple[list[str], np.ndarray, IDWMatrixEngine]:
        if seg not in self._cache:
            meta = stations_at(self.bounds[seg].to_pydatetime())
            ids = sorted(meta)
            pts = {s: {"name": s, "lat": meta[s].latitude, "lon": meta[s].longitude} for s in ids}
            dist = np.array(
                [
                    [
                        haversine_distance_km(
                            meta[a].latitude, meta[a].longitude, meta[b].latitude, meta[b].longitude
                        )
                        for b in ids
                    ]
                    for a in ids
                ]
            )
            # IDW with the same p / rebalancing as the pipeline; stations double as targets and
            # the self-weight is removed so each gauge is predicted from the others only.
            engine = IDWMatrixEngine(zones=pts, stations=pts)
            engine.base_weights = engine.base_weights.copy()
            np.fill_diagonal(engine.base_weights, 0.0)
            self._cache[seg] = (ids, dist, engine)
        return self._cache[seg]


def _step_values(row: pd.Series, ids: list[str]) -> np.ndarray:
    return row.reindex(ids).to_numpy(dtype=np.float64)


def fit_pooled_variogram(
    steps: pd.DataFrame, geo: _Geometry
) -> tuple[Variogram, dict[str, object]]:
    """Fit exponential (used) and spherical (for comparison) on pooled normalised semivariances."""
    bins = SemivarianceBins()
    for ts, row in steps.iterrows():
        ids, dist, _ = geo.get(geo.segment_of(ts))  # type: ignore[arg-type]
        vals = _step_values(row, ids)
        ok = ~np.isnan(vals)
        if ok.sum() >= 5:
            bins.add_step(dist[np.ix_(ok, ok)], vals[ok])
    expo, expo_err = fit_variogram(bins, "exponential")
    sph, sph_err = fit_variogram(bins, "spherical")
    info: dict[str, object] = {
        "fit_steps_used": bins.steps_used,
        "fit_steps_skipped_constant": bins.steps_skipped_constant,
        "exponential": {**expo.__dict__, "weighted_rms_error": expo_err},
        "spherical": {**sph.__dict__, "weighted_rms_error": sph_err},
        "used": "exponential",
    }
    return expo, info


def evaluate(
    steps: pd.DataFrame, geo: _Geometry, kriger: OrdinaryKriger
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Leave-one-gauge-out predictions. Returns one row per (step, gauge) plus counts."""
    recs: list[pd.DataFrame] = []
    skipped_steps = 0
    for k, (ts, row) in enumerate(steps.iterrows()):
        ids, dist, engine = geo.get(geo.segment_of(ts))  # type: ignore[arg-type]
        vals = _step_values(row, ids)
        ok = ~np.isnan(vals)
        if ok.sum() < MIN_NEIGHBOURS + 1:
            skipped_steps += 1
            continue
        idw_all = engine.interpolate_matrix(vals[None, :])[0]  # (n_zones == n_stations,)
        idx = np.flatnonzero(ok)
        ok_pred = kriger.leave_one_out(dist[np.ix_(idx, idx)], vals[idx])
        recs.append(
            pd.DataFrame(
                {
                    "step": k,
                    "day": ts.strftime("%Y-%m-%d"),  # type: ignore[union-attr]
                    "obs": vals[idx] * TO_MMH,
                    "idw": idw_all[idx] * TO_MMH,
                    "ok": ok_pred * TO_MMH,
                }
            )
        )
    return pd.concat(recs, ignore_index=True), {"steps_skipped_few_gauges": skipped_steps}


def _boot_diff(
    sub: pd.DataFrame,
    e_idw: np.ndarray,
    e_ok: np.ndarray,
    key: str,
    rng: np.random.Generator,
    n_boot: int,
) -> tuple[int, list[float]]:
    """Percentile bootstrap of the RMSE difference, resampling clusters given by column ``key``."""
    per = (
        pd.DataFrame({key: sub[key].to_numpy(), "n": 1.0, "s_idw": e_idw**2, "s_ok": e_ok**2})
        .groupby(key)
        .sum()
    )
    cnt, s_idw, s_ok = per["n"].to_numpy(), per["s_idw"].to_numpy(), per["s_ok"].to_numpy()
    m = len(per)
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, m, m)
        tot = cnt[pick].sum()
        diffs[b] = np.sqrt(s_ok[pick].sum() / tot) - np.sqrt(s_idw[pick].sum() / tot)
    lo, hi = np.percentile(diffs, [5, 95])
    return m, [float(lo), float(hi)]


def _summarise(
    sub: pd.DataFrame, n_steps: int, rng: np.random.Generator, n_boot: int
) -> dict[str, object]:
    n = len(sub)
    if n == 0:
        return {"n_gauge_steps": 0}
    e_idw = (sub["idw"] - sub["obs"]).to_numpy()
    e_ok = (sub["ok"] - sub["obs"]).to_numpy()
    rmse_idw = float(np.sqrt(np.mean(e_idw**2)))
    rmse_ok = float(np.sqrt(np.mean(e_ok**2)))
    m, ci_step = _boot_diff(sub, e_idw, e_ok, "step", rng, n_boot)
    m_day, ci_day = _boot_diff(sub, e_idw, e_ok, "day", rng, n_boot)
    return {
        "n_gauge_steps": n,
        "n_time_steps": m,
        "rmse_idw_mmh": rmse_idw,
        "rmse_kriging_mmh": rmse_ok,
        "bias_idw_mmh": float(e_idw.mean()),
        "bias_kriging_mmh": float(e_ok.mean()),
        "rmse_diff_kriging_minus_idw_mmh": rmse_ok - rmse_idw,
        "rmse_diff_ci90_mmh": ci_step,
        "n_days": m_day,
        "rmse_diff_ci90_by_day_mmh": ci_day,
    }


def summarise(res: pd.DataFrame, n_steps: int, seed: int, n_boot: int) -> dict[str, object]:
    rng = np.random.default_rng(seed)
    groups = {
        "light (<10 mm/h)": res[res["obs"] < HEAVY_MMH],
        "heavy (>=10 mm/h)": res[res["obs"] >= HEAVY_MMH],
        "very heavy (>=30 mm/h)": res[res["obs"] >= VERY_HEAVY_MMH],
        "all": res,
    }
    return {name: _summarise(sub, n_steps, rng, n_boot) for name, sub in groups.items()}


def format_table(summary: dict[str, object]) -> str:
    lines = [
        f"{'group':<24}{'n':>8}{'RMSE IDW':>10}{'RMSE OK':>9}{'bias IDW':>10}{'bias OK':>9}"
        f"{'OK-IDW (90% CI, steps)':>30}{'(by day)':>18}"
    ]
    for name, s in summary.items():
        assert isinstance(s, dict)
        if not s.get("n_gauge_steps"):
            lines.append(f"{name:<24}{0:>8}")
            continue
        lo, hi = s["rmse_diff_ci90_mmh"]
        dlo, dhi = s["rmse_diff_ci90_by_day_mmh"]
        lines.append(
            f"{name:<24}{s['n_gauge_steps']:>8}{s['rmse_idw_mmh']:>10.2f}{s['rmse_kriging_mmh']:>9.2f}"
            f"{s['bias_idw_mmh']:>10.2f}{s['bias_kriging_mmh']:>9.2f}"
            f"{s['rmse_diff_kriging_minus_idw_mmh']:>10.2f} [{lo:.2f}, {hi:.2f}]    [{dlo:.2f}, {dhi:.2f}]"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Leave-one-gauge-out: IDW vs ordinary kriging")
    p.add_argument("--first-year", type=int, default=FIRST_YEAR)
    p.add_argument("--last-year", type=int, default=settings.last_training_year)
    p.add_argument("--storm-mm", type=float, default=2.0, help="max gauge 5-min reading, mm")
    p.add_argument("--n-fit", type=int, default=1000)
    p.add_argument("--n-eval", type=int, default=4000)
    p.add_argument("--boot", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = p.parse_args(argv)
    assert_training_window(a.first_year, a.last_year)

    storm, scanned = collect_storm_steps(a.first_year, a.last_year, a.storm_mm)
    rng = np.random.default_rng(a.seed)
    order = rng.permutation(len(storm))
    n_fit = min(a.n_fit, len(storm) // 5)
    n_eval = min(a.n_eval, len(storm) - n_fit)
    fit_steps = storm.iloc[np.sort(order[:n_fit])]
    eval_steps = storm.iloc[np.sort(order[n_fit : n_fit + n_eval])]

    geo = _Geometry(a.first_year, a.last_year)
    variogram, vinfo = fit_pooled_variogram(fit_steps, geo)
    kriger = OrdinaryKriger(variogram)
    res, extra = evaluate(eval_steps, geo, kriger)
    summary = summarise(res, len(eval_steps), a.seed, a.boot)

    out = {
        "window": f"{a.first_year}-01-01 to {a.last_year}-12-31 (2024+ held-out, not used)",
        "storm_definition": f"step with max gauge 5-min reading >= {a.storm_mm} mm",
        "steps_scanned": scanned,
        "storm_steps_available": len(storm),
        "fit_steps": len(fit_steps),
        "eval_steps": len(eval_steps),
        "min_neighbours": MIN_NEIGHBOURS,
        "seed": a.seed,
        "bootstrap_resamples": a.boot,
        "units": "mm/h-equivalent (5-min total x 12)",
        "variogram": vinfo,
        "kriging_calls": kriger.n_calls,
        "kriging_fallbacks": {
            "constant_field": kriger.n_fallback_constant,
            "too_few_gauges": kriger.n_fallback_few,
            "singular": kriger.n_fallback_singular,
            "total": kriger.n_fallback,
        },
        **extra,
        "results": summary,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2))
    print(f"Window {out['window']}")
    print(
        f"Storm steps: {len(storm)} of {scanned} scanned; variogram fit on {len(fit_steps)}, "
        f"validated on {len(eval_steps)}"
    )
    print(f"Variogram (exponential): {vinfo['exponential']}")
    print(f"Spherical for comparison: {vinfo['spherical']}")
    print(f"Kriging fallbacks: {out['kriging_fallbacks']} of {kriger.n_calls} calls")
    print(format_table(summary))
    print(f"Wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
