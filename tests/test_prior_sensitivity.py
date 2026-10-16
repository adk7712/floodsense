"""Offline tests for the prior-sensitivity CLI: argument parsing and dirty-tree detection."""

import subprocess
from typing import Any

import pytest

from floodsense.models import prior_sensitivity as ps


def _fake_run(stdout: str) -> Any:
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert cmd == ["git", "status", "--porcelain"]
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return run


def test_defaults_include_pre_registered_value() -> None:
    args = ps.build_parser().parse_args([])
    assert args.prior_sd == [0.05, 0.1, 0.25, 0.5]
    assert 0.5 in args.prior_sd
    assert args.date is None and args.out is None and args.allow_dirty is False


def test_parser_overrides() -> None:
    args = ps.build_parser().parse_args(
        ["--date", "2026-10-20", "--prior-sd", "0.1", "0.5", "--out", "x", "--allow-dirty"]
    )
    assert args.prior_sd == [0.1, 0.5]
    assert args.date == "2026-10-20"
    assert str(args.out) == "x"
    assert args.allow_dirty is True


def test_git_dirty_true_on_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps.subprocess, "run", _fake_run(" M src/a.py\n?? new.py\n"))
    assert ps.git_dirty() is True


def test_git_dirty_false_on_clean_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps.subprocess, "run", _fake_run("\n"))
    assert ps.git_dirty() is False


@pytest.mark.parametrize("exc", [OSError("no git"), subprocess.CalledProcessError(128, "git")])
def test_git_dirty_none_when_git_fails(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise exc

    monkeypatch.setattr(ps.subprocess, "run", boom)
    assert ps.git_dirty() is None


def test_main_refuses_dirty_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps, "git_dirty", lambda: True)

    def no_load() -> Any:
        raise AssertionError("must not load data on a dirty tree")

    monkeypatch.setattr(ps, "load_training_data", no_load)
    assert ps.main([]) == 2
