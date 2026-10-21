"""Guard for the held-out test set (CLAUDE.md rules 2 and 7). Offline, SYNTHETIC data only.

Catches: (A) a change to the pinned budgets or year constants; (B) any selection entry point that
builds a LabelledData or scores rows from 2024 or later (it fails if one skips `.development()`);
(C) new literal test years, new `test_start_year` readers or new direct parquet reads in the
selection code.
Does not catch: test data reached by a path the scan and the sentinel do not see (a new entry
point not listed here, raw files read by other modules, years computed at run time), nor a
deliberate edit of this file. It is a tripwire, not a proof.
"""

import ast
import os
from pathlib import Path

import pandas as pd
import pytest

from floodsense.common.config import Settings, settings
from floodsense.models import (
    artifact,
    compare_cutoffs,
    mentor_tests,
    prior_sensitivity,
    refit_champion,
    select_candidates,
    static_candidates,
    training,
)
from tests.conftest import ZONES

PINNED = {
    "false_alarm_budget_high": 2.5,
    "false_alarm_budget_moderate": 11.0,
    "last_training_year": 2023,
    "test_start_year": 2024,
    "cv_first_validation_year": 2020,
}

# --- A. Config pins ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PINNED))
def test_pinned_class_defaults(name: str) -> None:
    assert Settings.model_fields[name].default == PINNED[name], f"{name} default changed"


def test_pinned_values_without_env_or_dotenv(monkeypatch) -> None:
    for key in [k for k in os.environ if k.startswith("FLOODSENSE_")]:
        monkeypatch.delenv(key)
    fresh = Settings(_env_file=None)  # skips .env entirely
    for name, value in PINNED.items():
        assert getattr(fresh, name) == value, f"{name} is not {value}"


# --- B. Behavioural sentinel ------------------------------------------------------------------


@pytest.fixture
def budgets(monkeypatch):
    # Same values as test_mentor_tests.py; the synthetic data fits them for these entry points.
    monkeypatch.setattr(settings, "false_alarm_budget_high", 2.5)
    monkeypatch.setattr(settings, "false_alarm_budget_moderate", 11.0)


@pytest.fixture
def seen(data, monkeypatch) -> dict[str, list[int]]:
    """Records the latest year of every LabelledData built and every frame scored."""
    record: dict[str, list[int]] = {"labelled": [], "scored": [], "fit": []}
    orig_post_init = training.LabelledData.__post_init__
    orig_raw = artifact.FloodModel.raw_proba

    def post_init(self) -> None:
        orig_post_init(self)
        if len(self.year):
            record["labelled"].append(int(self.year.max()))
        if self.windows:
            record["labelled"].append(max(w.start_lo.year for w in self.windows))

    def raw_proba(self, features):
        ts = features["timestamp"].dt.tz_convert(settings.tzinfo)
        if len(ts):
            record["scored"].append(int(ts.dt.year.max()))
        return orig_raw(self, features)

    def spy_input(real):
        # Records the latest year of any LabelledData handed to a fitting/CV function, so fitting
        # directly on a pre-existing full-range object is seen even if no new LabelledData is built.
        def wrapper(*args, **kwargs):
            for a in (*args, *kwargs.values()):
                if isinstance(a, training.LabelledData) and len(a.year):
                    record["fit"].append(int(a.year.max()))
            return real(*args, **kwargs)

        return wrapper

    # Patch every models module that holds one of these functions (imported by name or defined).
    for mod in (
        training,
        select_candidates,
        compare_cutoffs,
        prior_sensitivity,
        mentor_tests,
        refit_champion,
        static_candidates,
    ):
        for fname in (
            "cross_validate",
            "fit_candidate",
            "fit_static_candidate",
            "validation_years",
        ):
            real = getattr(mod, fname, None)
            if callable(real):
                monkeypatch.setattr(mod, fname, spy_input(real))

    monkeypatch.setattr(training.LabelledData, "__post_init__", post_init)
    monkeypatch.setattr(artifact.FloodModel, "raw_proba", raw_proba)
    return record


@pytest.fixture
def zone_static_csv(tmp_path) -> Path:
    path = tmp_path / "zone_static.csv"
    pd.DataFrame({"ura_planning_area": ZONES, "built_up_land_pct": [0.0, 50.0, 100.0]}).to_csv(
        path, index=False
    )
    return path


def _entry_points(data, tmp_path, zone_static_csv, monkeypatch) -> dict:
    monkeypatch.setattr(prior_sensitivity, "git_dirty", lambda: False)

    def refit():
        monkeypatch.setattr(settings, "root_dir", tmp_path)
        return refit_champion.refit(data, "2026-01-01")

    return {
        "train": lambda: training.train(data, ["rule_rain60"]),
        "select": lambda: select_candidates.run_selection(
            data,
            out_dir=tmp_path,
            run_date="2026-01-01",
            candidates=["rule_rain60", "rule_rain60_static", "rule_rain60_nostatic"],
            save=False,
        ),
        "cutoffs": lambda: compare_cutoffs.run_comparison(data, run_date="2026-01-01"),
        "prior": lambda: prior_sensitivity.run_sensitivity(data, [0.5], run_date="2026-01-01"),
        "mentor": lambda: mentor_tests.run_tests(
            data, ["t1"], zone_static=zone_static_csv, run_date="2026-01-01"
        ),
        "refit": refit,
    }


@pytest.mark.parametrize("name", ["train", "select", "cutoffs", "prior", "mentor", "refit"])
def test_entry_point_never_sees_test_years(
    name, seen, budgets, data, tmp_path, zone_static_csv, monkeypatch
) -> None:
    _entry_points(data, tmp_path, zone_static_csv, monkeypatch)[name]()
    assert seen["labelled"], "sentinel recorded nothing: it is not wired to the entry point"
    limit = settings.last_training_year
    assert max(seen["labelled"]) <= limit, f"{name} built data from year {max(seen['labelled'])}"
    assert seen["scored"], f"{name}: scoring spy recorded nothing (dead spy?)"
    assert seen["fit"], f"{name}: fit/CV spy recorded nothing (dead spy?)"
    assert max(seen["scored"]) <= limit, f"{name} scored year {max(seen['scored'])}"
    assert max(seen["fit"]) <= limit, f"{name} fitted on year {max(seen['fit'])}"


def test_sentinel_can_fail(seen, data) -> None:
    data.subset(data.year >= settings.test_start_year)
    assert max(seen["labelled"]) >= PINNED["test_start_year"]


def test_final_report_is_the_only_reader(seen, budgets, data) -> None:
    run = training.train(data, ["rule_rain60"])
    assert max(seen["labelled"] + seen["scored"] + seen["fit"]) < PINNED["test_start_year"]
    for v in seen.values():
        v.clear()
    training.final_report(run.model, data)
    assert max(seen["labelled"] + seen["scored"]) >= PINNED["test_start_year"]


# --- C. Static AST scan -----------------------------------------------------------------------

SRC = Path(__file__).resolve().parents[1] / "src" / "floodsense"
SCANNED = sorted(
    [
        *(SRC / "models").glob("*.py"),
        SRC / "features" / "krig_upper.py",
        SRC / "spatial" / "compare_interpolation.py",
        SRC / "labels" / "policy.py",
    ]
)

R2_ALLOWED = {
    "training.py::LabelledData.development",
    "training.py::final_report",
    "compare_interpolation.py::assert_training_window",
}
R3_ALLOWED = {
    "mentor_tests.py::krig_upper_input",
    "krig_upper.py::build_month",
}


def _scan() -> dict[str, set[str]]:
    hits: dict[str, set[str]] = {"R1": set(), "R2": set(), "R3": set()}

    class Visitor(ast.NodeVisitor):
        def __init__(self, fname: str) -> None:
            self.fname = fname
            self.stack: list[str] = []

        def where(self, node) -> str:
            return f"{self.fname}::{'.'.join(self.stack) or '<module>'}"

        def scope(self, node) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = scope

        def visit_Constant(self, node) -> None:
            if type(node.value) is int and 2024 <= node.value <= 2026:
                hits["R1"].add(f"{self.where(node)} line {node.lineno} ({node.value})")

        def visit_Attribute(self, node) -> None:
            if node.attr == "test_start_year":
                hits["R2"].add(self.where(node))
            self.generic_visit(node)

        def visit_Call(self, node) -> None:
            f = node.func
            if isinstance(f, ast.Attribute):
                # A glob only counts when it looks for parquet; artifact.py globs *.joblib model
                # files, which are not data.
                is_parquet_glob = f.attr == "glob" and any(
                    isinstance(a, ast.Constant) and "parquet" in str(a.value) for a in node.args
                )
                if f.attr in {"read_parquet", "read_table"} or is_parquet_glob:
                    hits["R3"].add(self.where(node))
            self.generic_visit(node)

    for path in SCANNED:
        Visitor(path.name).visit(ast.parse(path.read_text(), filename=str(path)))
    return hits


def test_scan_covers_expected_files() -> None:
    assert all(p.exists() for p in SCANNED) and len(SCANNED) >= 10


def test_no_literal_test_years_in_selection_code() -> None:
    assert not _scan()["R1"]


def test_only_allowed_functions_read_test_start_year() -> None:
    extra = _scan()["R2"] - R2_ALLOWED
    assert not extra, f"new test_start_year readers: {sorted(extra)}"


def test_only_allowed_functions_read_parquet_directly() -> None:
    extra = _scan()["R3"] - R3_ALLOWED
    assert not extra, f"new direct file reads: {sorted(extra)}"
