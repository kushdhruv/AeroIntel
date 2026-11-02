import importlib.util
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber
from moto import mock_aws

from adapters.events.aws import EventBridge
from adapters.events.cloudwatch import CloudWatch
from adapters.storage.s3 import S3Storage
from adapters.training.sagemaker import SageMaker, SageMakerInference
from app.core.errors import Conflict, Forbidden, Invalid
from app.core.platform import Platform
from app.core.security import Principal

ARN = "arn:aws:sagemaker:us-east-1:123456789012:model-package/aerointel/1"


def client(service: str):
    return boto3.client(
        service,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


@mock_aws
def test_s3_integrity_conditional_write_and_mutation_guard() -> None:
    s3 = client("s3")
    s3.create_bucket(Bucket="aerointel-unit-tests")
    storage = S3Storage(s3, "aerointel-unit-tests", allow_mutations=True)
    assert storage.write("sample.bin", b"first").startswith("s3://")
    storage.write("sample.bin", b"first")
    assert storage.read("sample.bin") == b"first"
    with pytest.raises(Conflict):
        storage.write("sample.bin", b"second")
    with pytest.raises(Forbidden):
        S3Storage(s3, "aerointel-unit-tests").write("x", b"blocked")
    s3.put_object(Bucket="aerointel-unit-tests", Key="aerointel/sample.bin", Body=b"tampered")
    with pytest.raises(Invalid):
        storage.read("sample.bin")


@pytest.mark.parametrize("key", ["../secret", "a\\b", "/absolute", "s3://other/object"])
def test_s3_prefix_escape_rejected(key: str) -> None:
    with pytest.raises(Invalid):
        S3Storage(None, "test").key(key)


def dataset_event(platform: Platform) -> dict:
    with platform.db.transaction() as db:
        return json.loads(
            db.execute("SELECT body FROM outbox ORDER BY rowid LIMIT 1").fetchone()[0]
        )


def test_eventbridge_failures_are_not_acknowledged(trained: Platform) -> None:
    events = client("events")
    event = dataset_event(trained)
    with Stubber(events) as stub:
        stub.add_response(
            "put_events",
            {
                "FailedEntryCount": 1,
                "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "injected"}],
            },
            {"Entries": ANY},
        )
        with pytest.raises(Invalid):
            EventBridge(events, "aerointel", True).publish(event)
        stub.assert_no_pending_responses()
    with pytest.raises(Forbidden):
        EventBridge(events, "aerointel").publish(event)


def test_sagemaker_pipeline_submission_and_status(trained: Platform) -> None:
    sagemaker = client("sagemaker")
    arn = "arn:aws:sagemaker:us-east-1:123456789012:pipeline/aerointel/execution/123"
    with Stubber(sagemaker) as stub:
        stub.add_response(
            "start_pipeline_execution",
            {"PipelineExecutionArn": arn},
            {
                "PipelineName": "aerointel",
                "ClientRequestToken": ANY,
                "PipelineParameters": [
                    {"Name": "ManifestUri", "Value": "s3://bucket/aerointel/input/manifest.json"}
                ],
            },
        )
        stub.add_response(
            "describe_pipeline_execution",
            {"PipelineExecutionStatus": "Succeeded"},
            {"PipelineExecutionArn": arn},
        )
        adapter = SageMaker(sagemaker, trained.registry, True)
        assert (
            adapter.start_pipeline(
                "aerointel", "s3://bucket/aerointel/input/manifest.json", "logical"
            )
            == arn
        )
        assert adapter.pipeline_status(arn)["status"] == "Succeeded"
        stub.assert_no_pending_responses()
    with pytest.raises(Forbidden):
        SageMaker(sagemaker, trained.registry).start_pipeline("x", "s3://x", "run")


@mock_aws
def test_cloud_registry_packages_actual_evaluated_artifact_pending_approval(
    trained: Platform,
) -> None:
    model = trained.db.list("model")[0]
    key = f"{model['model_id']}:{model['model_version']}"
    s3 = client("s3")
    s3.create_bucket(Bucket="aerointel-unit-tests")
    storage = S3Storage(s3, "aerointel-unit-tests", allow_mutations=True)
    sagemaker = client("sagemaker")
    with Stubber(sagemaker) as stub:
        stub.add_response(
            "create_model_package",
            {"ModelPackageArn": ARN},
            {
                "ModelPackageGroupName": "aerointel",
                "ModelApprovalStatus": "PendingManualApproval",
                "InferenceSpecification": ANY,
                "CustomerMetadataProperties": {
                    "artifact_sha256": model["artifact_sha256"],
                    "evaluation_id": model["evaluation_id"],
                    "model_key": key,
                },
                "ClientToken": ANY,
            },
        )
        assert (
            SageMaker(sagemaker, trained.registry, True).register(
                key, "aerointel", "reviewed-image", storage
            )
            == ARN
        )
        stub.assert_no_pending_responses()
    objects = s3.list_objects_v2(Bucket="aerointel-unit-tests")["Contents"]
    assert len(objects) == 1 and objects[0]["Key"].endswith("model.tar.gz")
    with pytest.raises(Forbidden):
        SageMaker(sagemaker, trained.registry, True).approve_package(key, ARN)


def package(model: dict) -> dict:
    return {
        "ModelPackageName": "aerointel",
        "ModelPackageStatusDetails": {"ValidationStatuses": []},
        "ModelPackageArn": ARN,
        "CreationTime": datetime.now(UTC),
        "ModelPackageStatus": "Completed",
        "ModelApprovalStatus": "Approved",
        "CustomerMetadataProperties": {
            "model_key": f"{model['model_id']}:{model['model_version']}",
            "artifact_sha256": model["artifact_sha256"],
            "evaluation_id": model["evaluation_id"],
        },
    }


def test_cloud_approval_deploy_canary_and_health(trained: Platform) -> None:
    model = trained.db.list("model")[0]
    key = f"{model['model_id']}:{model['model_version']}"
    trained.registry.approve(key, Principal("reviewer", "approver", True), "human")
    sagemaker = client("sagemaker")
    with Stubber(sagemaker) as stub:
        stub.add_response("describe_model_package", package(model), {"ModelPackageName": ARN})
        stub.add_response(
            "update_model_package",
            {"ModelPackageArn": ARN},
            {"ModelPackageArn": ARN, "ModelApprovalStatus": "Approved"},
        )
        adapter = SageMaker(sagemaker, trained.registry, True)
        adapter.approve_package(key, ARN)
        stub.add_response("describe_model_package", package(model), {"ModelPackageName": ARN})
        stub.add_response(
            "create_model",
            {"ModelArn": "arn:aws:sagemaker:us-east-1:123456789012:model/test"},
            {
                "ModelName": ANY,
                "ExecutionRoleArn": "arn:aws:iam::123456789012:role/test",
                "PrimaryContainer": {
                    "ModelPackageName": ARN,
                    "Environment": {"AEROINTEL_ARTIFACT_SHA256": model["artifact_sha256"]},
                },
                "EnableNetworkIsolation": True,
            },
        )
        stub.add_response(
            "create_endpoint_config",
            {"EndpointConfigArn": "arn:aws:sagemaker:us-east-1:123456789012:endpoint-config/test"},
            {"EndpointConfigName": ANY, "ProductionVariants": ANY},
        )
        endpoint = {
            "EndpointName": "aerointel",
            "EndpointArn": "arn:aws:sagemaker:us-east-1:123456789012:endpoint/aerointel",
            "EndpointConfigName": "previous",
            "EndpointStatus": "InService",
            "CreationTime": datetime.now(UTC),
            "LastModifiedTime": datetime.now(UTC),
        }
        stub.add_response("describe_endpoint", endpoint, {"EndpointName": "aerointel"})
        stub.add_response(
            "update_endpoint",
            {"EndpointArn": endpoint["EndpointArn"]},
            {
                "EndpointName": "aerointel",
                "EndpointConfigName": ANY,
                "DeploymentConfig": {
                    "BlueGreenUpdatePolicy": {
                        "TrafficRoutingConfiguration": {
                            "Type": "CANARY",
                            "WaitIntervalInSeconds": 300,
                            "CanarySize": {"Type": "CAPACITY_PERCENT", "Value": 10},
                        },
                        "TerminationWaitInSeconds": 300,
                        "MaximumExecutionTimeoutInSeconds": 3600,
                    },
                    "AutoRollbackConfiguration": {"Alarms": [{"AlarmName": "errors"}]},
                },
            },
        )
        assert adapter.deploy(
            key, ARN, "aerointel", "arn:aws:iam::123456789012:role/test", ["errors"]
        )
        stub.add_response("describe_endpoint", endpoint, {"EndpointName": "aerointel"})
        assert adapter.health("aerointel") is True
        stub.assert_no_pending_responses()


def test_cloud_runtime_and_missing_label_metrics() -> None:
    runtime = client("sagemaker-runtime")
    with Stubber(runtime) as stub:
        stub.add_response(
            "invoke_endpoint",
            {"Body": io.BytesIO(b'{"change_percentage": 10}')},
            {
                "EndpointName": "aerointel",
                "ContentType": "application/json",
                "Body": b'{"before": "test"}',
            },
        )
        assert (
            SageMakerInference(runtime, "aerointel").predict({"before": "test"})[
                "change_percentage"
            ]
            == 10
        )
    cloudwatch = client("cloudwatch")
    with Stubber(cloudwatch) as stub:
        stub.add_response(
            "put_metric_data",
            {},
            {"Namespace": "AeroIntel", "MetricData": [{"MetricName": "InputDrift", "Value": 1.0}]},
        )
        CloudWatch(cloudwatch, True).publish(
            {"input_shift": "detected", "performance": {"iou": None}}
        )
        stub.assert_no_pending_responses()


def test_lambda_disabled_by_default_and_duplicate_events_share_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path(__file__).resolve().parents[2] / "infrastructure/terraform/lambda/handler.py"
    spec = importlib.util.spec_from_file_location("event_handler", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.handler({}, None) == {"status": "disabled"}
    monkeypatch.setenv("CLOUD_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PIPELINE_NAME", "aerointel")
    calls = []

    class Fake:
        def start_pipeline_execution(self, **kwargs):
            calls.append(kwargs)
            return {"PipelineExecutionArn": "same-run"}

    event = {
        "detail": {
            "schema_version": "1.0.0",
            "event_type": "dataset-batch-completed",
            "payload": {"manifest_uri": "s3://bucket/aerointel/input/manifest.json"},
        }
    }
    module.handler(event, None, Fake())
    module.handler(event, None, Fake())
    assert calls[0]["ClientRequestToken"] == calls[1]["ClientRequestToken"]
