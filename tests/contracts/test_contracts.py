import copy
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import ValidationError

from app.api.main import app
from app.core.config import load_settings
from contracts.validation import KINDS, validate_contract, validator
from modules.geoleak.gates import evaluate_gates

ROOT = Path(__file__).resolve().parents[2]


def report() -> dict:
    metrics = {"iou": 0.8, "f1": 0.85, "precision": 0.9, "recall": 0.8, "test_samples": 10}
    return {
        "schema_version": "1.0.0",
        "evaluation_id": "eval-001",
        "dataset_id": "synthetic",
        "dataset_version": "v001",
        "model_id": "siamese",
        "model_version": "v001",
        "created_at": "2026-11-02T00:00:00Z",
        "policy_id": "geoleak-v1",
        "geographic_metadata_available": True,
        "split_assignment_sha256": "sha256:" + "a" * 64,
        "splits": {name: copy.deepcopy(metrics) for name in ("random", "scene", "spatial")},
        "leakage": {
            "duplicate_pairs": 0,
            "scene_leaks": 0,
            "overlapping_tiles": 0,
            "overlap_check_status": "completed",
        },
        "decision": "PASS",
        "reasons": [],
        "limitations": [],
    }


@pytest.mark.parametrize("kind", KINDS)
def test_every_schema_compiles(kind: str) -> None:
    validator(kind)


def test_spatial_failure_blocks_even_with_excellent_random_accuracy() -> None:
    document = report()
    document["splits"]["random"]["iou"] = 0.99
    document["splits"]["spatial"]["iou"] = 0.4
    # Deliberately forged PASS must not be trusted by the evaluator.
    assert evaluate_gates(document).decision == "FAIL"


@pytest.mark.parametrize("field", ["duplicate_pairs", "scene_leaks", "overlapping_tiles"])
def test_leakage_blocks_gate(field: str) -> None:
    document = report()
    document["leakage"][field] = 1
    assert evaluate_gates(document).decision == "FAIL"


def test_missing_geography_requires_review() -> None:
    document = report()
    document["geographic_metadata_available"] = False
    document["splits"]["spatial"] = None
    document["leakage"]["overlap_check_status"] = "unavailable"
    assert evaluate_gates(document).decision == "REVIEW_REQUIRED"


def test_unknown_overlap_check_requires_review() -> None:
    document = report()
    document["leakage"]["overlap_check_status"] = "unavailable"
    assert evaluate_gates(document).decision == "REVIEW_REQUIRED"


def test_valid_evidence_passes() -> None:
    assert evaluate_gates(report()).decision == "PASS"


def test_spatial_claim_without_geography_is_invalid() -> None:
    document = report()
    document["geographic_metadata_available"] = False
    with pytest.raises(ValidationError):
        validate_contract("evaluation-report", document)


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": "2.0.0"},
        {"created_at": "yesterday"},
        {"unexpected": True},
    ],
)
def test_invalid_report_rejected(mutation: dict) -> None:
    with pytest.raises(ValidationError):
        validate_contract("evaluation-report", report() | mutation)


@pytest.mark.parametrize("value", [-0.1, 1.1, "0.8", None, float("nan"), float("inf")])
def test_invalid_metrics_rejected(value: object) -> None:
    document = report()
    document["splits"]["spatial"]["iou"] = value
    with pytest.raises(ValidationError):
        evaluate_gates(document)


@pytest.mark.parametrize("state", ["approved", "deployed"])
def test_active_state_requires_approval(state: str) -> None:
    document = {
        "schema_version": "1.0.0",
        "model_id": "test",
        "model_version": "v001",
        "architecture": "siamese_unet",
        "dataset_id": "test",
        "dataset_version": "v001",
        "artifact_uri": "artifacts/model.pt",
        "artifact_sha256": "sha256:" + "a" * 64,
        "evaluation_id": "eval-001",
        "created_at": "2026-11-02T00:00:00Z",
        "state": state,
        "approval": None,
    }
    with pytest.raises(ValidationError):
        validate_contract("model-manifest", document)


def test_dataset_example_and_null_crs() -> None:
    document = json.loads((ROOT / "specs/examples/dataset-manifest.json").read_text())
    validate_contract("dataset-manifest", document)
    document["samples"][0]["metadata"]["crs"] = "definitely-not-a-crs"
    with pytest.raises(ValidationError):
        validate_contract("dataset-manifest", document)


def test_footprint_without_crs_rejected() -> None:
    document = json.loads((ROOT / "specs/examples/dataset-manifest.json").read_text())
    document["samples"][0]["metadata"]["bounds"] = [0, 0, 1, 1]
    with pytest.raises(ValidationError):
        validate_contract("dataset-manifest", document)


@pytest.mark.parametrize("kind", [k for k in KINDS if KINDS[k] == "events"])
def test_event_discriminator_rejects_wrong_type(kind: str) -> None:
    schema = validator(kind).schema
    assert schema["properties"]["event_type"] == {"const": kind}
    with pytest.raises(ValidationError):
        validate_contract(kind, {"schema_version": "1.0.0", "event_type": "wrong"})


def test_health_and_openapi_snapshot() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "contract_version": "1.0.0"}
    snapshot = yaml.safe_load((ROOT / "specs/api/openapi.yaml").read_text())
    assert snapshot == app.openapi()


def test_local_defaults_do_not_enable_cloud() -> None:
    settings = load_settings(ROOT / "configs/local.yaml")
    assert settings.execution_mode == "local"
    assert settings.cloud_execution_enabled is False


def test_unknown_contract_rejected() -> None:
    with pytest.raises(ValueError):
        validate_contract("../secrets", {})
