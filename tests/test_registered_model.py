"""Loading the model registered in Unity Catalog (floodsense.models.artifact), with mlflow's
download faked: no network, no workspace."""

import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import joblib
import pytest

from floodsense.common.config import settings
from floodsense.models import artifact
from floodsense.models.artifact import FloodModel, load_registered_model, registered_version


def fake_mlflow(monkeypatch, model_file: Path, version: str = "1") -> SimpleNamespace:
    """An ``mlflow`` module whose download lays out a registered model like the real one."""
    calls = SimpleNamespace(uris=[], registry=None)

    def download_artifacts(artifact_uri: str, dst_path: str) -> str:
        calls.uris.append(artifact_uri)
        (Path(dst_path) / "artifacts").mkdir(parents=True)
        shutil.copy(model_file, Path(dst_path) / "artifacts" / "flood_model.joblib")
        return dst_path

    def set_registry_uri(uri: str) -> None:
        calls.registry = uri

    class MlflowClient:
        def __init__(self, registry_uri: str):
            calls.registry = registry_uri

        def get_model_version_by_alias(self, name: str, alias: str):
            calls.uris.append(f"{name}@{alias}")
            return SimpleNamespace(version=version)

    module = ModuleType("mlflow")
    module.artifacts = SimpleNamespace(download_artifacts=download_artifacts)
    module.set_registry_uri = set_registry_uri
    module.MlflowClient = MlflowClient
    monkeypatch.setitem(sys.modules, "mlflow", module)
    return calls


@pytest.fixture
def committed_model() -> Path:
    if not settings.flood_model_path.exists():
        pytest.skip("models/flood_model.joblib not present")
    return settings.flood_model_path


def test_the_registered_model_is_the_floodmodel_scoring_expects(monkeypatch, committed_model):
    calls = fake_mlflow(monkeypatch, committed_model)
    model = load_registered_model()
    assert isinstance(model, FloodModel)
    assert model.thresholds == joblib.load(committed_model).thresholds
    assert calls.uris == [artifact.REGISTERED_MODEL_URI]
    assert calls.registry == "databricks-uc"


def test_a_non_floodmodel_is_refused(monkeypatch, tmp_path):
    wrong = tmp_path / "wrong.joblib"
    joblib.dump({"not": "a model"}, wrong)
    fake_mlflow(monkeypatch, wrong)
    with pytest.raises(TypeError, match="does not hold a FloodModel"):
        load_registered_model("models:/workspace.floodsense.flood_model/3")


def test_registered_version(monkeypatch, committed_model):
    calls = fake_mlflow(monkeypatch, committed_model, version="4")
    assert registered_version() == "4"
    assert calls.uris == ["workspace.floodsense.flood_model@champion"]
    assert registered_version("models:/workspace.floodsense.flood_model/2") == "2"


def test_a_version_logged_under_another_file_name_still_loads(
    monkeypatch, committed_model, tmp_path
):
    renamed = tmp_path / "rule_rain60_refit_2026-10-17.joblib"
    shutil.copy(committed_model, renamed)

    calls = fake_mlflow(monkeypatch, committed_model)

    def download_artifacts(artifact_uri: str, dst_path: str) -> str:
        calls.uris.append(artifact_uri)
        (Path(dst_path) / "artifacts").mkdir(parents=True)
        shutil.copy(renamed, Path(dst_path) / "artifacts" / renamed.name)
        return dst_path

    sys.modules["mlflow"].artifacts.download_artifacts = download_artifacts
    assert isinstance(
        load_registered_model("models:/workspace.floodsense.flood_model/2"), FloodModel
    )
