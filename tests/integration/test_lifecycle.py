import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError

from adapters.events.local import dispatch
from adapters.storage.local import digest
from app.core.errors import Conflict, Forbidden, Invalid, NotFound
from app.core.platform import Platform
from app.core.security import Principal
from modules.modelops.service import ModelOps
from modules.propertypulse.training.engine import TrainingConfig

OPERATOR = Principal("operator", "operator")
HUMAN = Principal("reviewer", "approver", True)


def model_key(platform: Platform) -> str:
    model = platform.db.list("model")[0]
    return f"{model['model_id']}:{model['model_version']}"


def clone_candidate(platform: Platform, version: str, weak: bool = False) -> str:
    model = copy.deepcopy(platform.db.list("model")[0])
    report = copy.deepcopy(platform.db.get("evaluation", model["evaluation_id"]))
    lineage = copy.deepcopy(platform.db.get("lineage", model["evaluation_id"]))
    report.update({"evaluation_id": version, "model_version": version})
    if weak:
        report["splits"]["random"]["iou"] = 0.99
        report["splits"]["spatial"]["iou"] = 0.1
    model.update({"evaluation_id": version, "model_version": version})
    with platform.db.transaction() as db:
        platform.db.put(db, "evaluation", version, report)
        platform.db.put(db, "lineage", version, lineage)
    if weak:
        # Simulate a candidate imported by a legacy registry; approval must still refuse it.
        with platform.db.transaction() as db:
            platform.db.put(db, "model", f"propertypulse:{version}", model)
    else:
        platform.registry.register(model)
    return f"propertypulse:{version}"


def test_training_never_approves_or_deploys(trained: Platform) -> None:
    assert trained.registry.versions()[0]["state"] == "pending_approval"
    with pytest.raises(NotFound):
        trained.deployment.active()
    with pytest.raises(Forbidden):
        trained.deployment.deploy(model_key(trained), OPERATOR, "unapproved")
    assert trained.db.history()[-1]["decision"] == "denied"


def test_failed_spatial_gate_cannot_be_approved_or_deployed(trained: Platform) -> None:
    key = clone_candidate(trained, "weak", weak=True)
    with pytest.raises(Forbidden, match="Quality gate"):
        trained.registry.approve(key, HUMAN, "weak-approval")
    with pytest.raises(Forbidden):
        trained.deployment.deploy(key, OPERATOR, "weak-deploy")
    with pytest.raises(NotFound):
        trained.db.get("approval", key)


def test_artifact_tampering_blocks_approval(trained: Platform) -> None:
    model = trained.db.list("model")[0]
    trained.storage.path(model["artifact_uri"]).write_bytes(b"tampered")
    with pytest.raises(Invalid, match="Checksum"):
        trained.registry.approve(model_key(trained), HUMAN, "tamper")


def test_policy_changes_invalidate_candidate(trained: Platform) -> None:
    trained.registry.policy["minimum_metrics"]["spatial"]["iou"] = 0.99
    with pytest.raises(Forbidden):
        trained.registry.approve(model_key(trained), HUMAN, "policy-changed")


def test_human_presence_and_role_are_required(trained: Platform) -> None:
    for actor in (OPERATOR, Principal("agent", "approver", False), Principal("reader", "reader")):
        with pytest.raises(Forbidden):
            trained.registry.approve(model_key(trained), actor, str(uuid4()))
    assert trained.db.list("approval") == []


def test_failed_canary_preserves_active_and_rollback_works(trained: Platform) -> None:
    first = model_key(trained)
    trained.registry.approve(first, HUMAN, "approve-first")
    deployed = trained.deployment.deploy(first, OPERATOR, "deploy-first")
    assert deployed["status"] == "active"
    second = clone_candidate(trained, "candidate")
    trained.registry.approve(second, HUMAN, "approve-second")
    failed = trained.deployment.deploy(second, OPERATOR, "canary-fail", canary=lambda _: False)
    assert failed["status"] == "failed"
    assert trained.deployment.active() == first
    assert trained.deployment.deploy(second, OPERATOR, "canary-good")["status"] == "active"
    assert trained.deployment.active() == second
    assert trained.deployment.rollback(OPERATOR, "rollback")["status"] == "active"
    assert trained.deployment.active() == first
    assert len(trained.deployment.history()) == 4


def test_duplicate_event_and_parallel_submit_create_one_logical_run(trained: Platform) -> None:
    with trained.db.transaction() as db:
        event = json.loads(
            db.execute("SELECT body FROM outbox ORDER BY rowid LIMIT 1").fetchone()[0]
        )
    config = TrainingConfig()
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(
            pool.map(lambda _: trained.pipeline.consume_dataset_event(event, config), range(8))
        )
    assert len(set(ids)) == 1
    status = trained.pipeline.execute(ids[0])
    assert status["state"] == "COMPLETED" and status["attempts"] == 1


def test_outbox_failures_retry_without_duplicate_job(trained: Platform) -> None:
    def failed(_: dict) -> None:
        raise RuntimeError("Injected publish failure")

    with pytest.raises(RuntimeError):
        dispatch(trained.db, failed)
    created = []

    def consumer(event: dict) -> None:
        if event["event_type"] == "dataset-batch-completed":
            created.append(trained.pipeline.consume_dataset_event(event, TrainingConfig()))

    assert dispatch(trained.db, consumer) > 0
    assert dispatch(trained.db, consumer) == 0
    assert len(set(created)) == 1


@pytest.mark.parametrize("stage", ["validate", "prepare", "train", "evaluate", "register"])
def test_interrupted_stages_resume_safely(trained: Platform, stage: str) -> None:
    # New parameter set creates a real second run. Reuse checkpoints by failing before training,
    # then validate recovery from the same persisted stage through completion or gate rejection.
    dataset = trained.db.list("dataset")[0]
    key = f"{dataset['dataset_id']}:{dataset['dataset_version']}"
    run_id = trained.pipeline.submit(key, TrainingConfig(epochs=1))

    def interrupt(current: str) -> None:
        if current == stage:
            raise RuntimeError("Injected process interruption")

    with pytest.raises(RuntimeError):
        trained.pipeline.execute(run_id, failpoint=interrupt)
    status = trained.pipeline.status(run_id)
    assert status["state"] == "FAILED" and status["stage"] == stage
    assert trained.pipeline.execute(run_id)["state"] in {"COMPLETED", "REJECTED"}
    assert trained.pipeline.status(run_id)["attempts"] == 2
    # The earlier registered model was never corrupted by the second run's failure.
    original = trained.db.list("model")[0]
    trained.storage.read(original["artifact_uri"], original["artifact_sha256"])


def test_registry_and_audit_are_immutable(trained: Platform) -> None:
    model = trained.db.list("model")[0]
    key = model_key(trained)
    model["artifact_sha256"] = digest(b"different")
    with trained.db.transaction() as db, pytest.raises(Conflict):
        trained.db.put(db, "model", key, model)
    with trained.db.transaction() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM audit")


def test_mcp_denials_validation_and_proposal_idempotency(trained: Platform) -> None:
    ops, key = ModelOps(trained), model_key(trained)
    for tool, args, actor in [
        ("request_model_promotion", {"target": key, "request_id": "unapproved"}, OPERATOR),
        (
            "request_model_promotion",
            {"target": key, "request_id": "reader"},
            Principal("agent", "reader"),
        ),
        ("create_retraining_proposal", {"target": key, "request_id": "no-evidence"}, OPERATOR),
        ("unrestricted_aws_api", {"target": key}, OPERATOR),
        ("get_pipeline_status", {"target": 123}, OPERATOR),
        ("get_geoleak_report", {"target": key, "role": "approver"}, OPERATOR),
    ]:
        with pytest.raises((Forbidden, Invalid, ValidationError)):
            ops.call(tool, args, actor)
        assert trained.db.history()[-1]["decision"] == "denied"
    trained.registry.approve(key, HUMAN, "human")
    arguments = {"target": key, "request_id": "request"}
    first = ops.call("request_model_promotion", arguments, OPERATOR)
    assert first == ops.call("request_model_promotion", arguments, OPERATOR)
    assert len(trained.db.list("proposal")) == 1
    with pytest.raises(NotFound):
        trained.deployment.active()
