"""Narrow boto3 APIs; no generic operation executor or automatic human approval."""

import gzip
import hashlib
import io
import json
import tarfile
from typing import Any

from adapters.storage.s3 import S3Storage
from app.core.errors import Forbidden, Invalid
from modules.registry.service import Registry


class SageMaker:
    def __init__(self, client: Any, registry: Registry, allow_mutations: bool = False) -> None:
        self.client, self.registry = client, registry
        self.allow_mutations = allow_mutations

    def _write(self) -> None:
        if not self.allow_mutations:
            raise Forbidden("Cloud mutations require explicit deployment authorization")

    def start_pipeline(self, name: str, manifest_uri: str, run_id: str) -> str:
        self._write()
        if not manifest_uri.startswith("s3://"):
            raise Invalid("SageMaker pipeline requires an S3 manifest")
        response = self.client.start_pipeline_execution(
            PipelineName=name,
            ClientRequestToken=hashlib.sha256(run_id.encode()).hexdigest(),
            PipelineParameters=[{"Name": "ManifestUri", "Value": manifest_uri}],
        )
        return str(response["PipelineExecutionArn"])

    def pipeline_status(self, execution_arn: str) -> dict[str, Any]:
        response = self.client.describe_pipeline_execution(PipelineExecutionArn=execution_arn)
        return {"execution_arn": execution_arn, "status": response["PipelineExecutionStatus"]}

    def register(self, model_key: str, group: str, image: str, storage: S3Storage) -> str:
        self._write()
        model = self.registry.db.get("model", model_key)
        self.registry.eligible(model)
        # Build the archive from the artifact actually evaluated by the backend, never an
        # arbitrary caller-supplied S3 URI carrying forged lineage metadata.
        data = self.registry.storage.read(model["artifact_uri"], model["artifact_sha256"])
        archive = io.BytesIO()
        with gzip.GzipFile(fileobj=archive, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tar:
                member = tarfile.TarInfo("model.pt")
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        artifact_s3_uri = storage.write(
            f"models/{model['artifact_sha256'][7:]}/model.tar.gz", archive.getvalue()
        )
        response = self.client.create_model_package(
            ModelPackageGroupName=group,
            ModelApprovalStatus="PendingManualApproval",
            InferenceSpecification={
                "Containers": [{"Image": image, "ModelDataUrl": artifact_s3_uri}],
                "SupportedContentTypes": ["application/json"],
                "SupportedResponseMIMETypes": ["application/json"],
            },
            CustomerMetadataProperties={
                "artifact_sha256": model["artifact_sha256"],
                "evaluation_id": model["evaluation_id"],
                "model_key": model_key,
            },
            ClientToken=hashlib.sha256(model_key.encode()).hexdigest(),
        )
        return str(response["ModelPackageArn"])

    def approve_package(self, model_key: str, package_arn: str) -> None:
        self._write()
        model = self.registry.approved(model_key)
        description = self.client.describe_model_package(ModelPackageName=package_arn)
        metadata = description.get("CustomerMetadataProperties", {})
        if metadata.get("model_key") != model_key or any(
            metadata.get(field) != model[field] for field in ("artifact_sha256", "evaluation_id")
        ):
            raise Forbidden("Cloud model package lineage does not match human-approved model")
        self.client.update_model_package(
            ModelPackageArn=package_arn, ModelApprovalStatus="Approved"
        )

    def deploy(
        self,
        model_key: str,
        package_arn: str,
        endpoint: str,
        role: str,
        alarm_names: list[str],
        instance_type: str = "ml.m5.large",
    ) -> str:
        self._write()
        model = self.registry.approved(model_key)
        package = self.client.describe_model_package(ModelPackageName=package_arn)
        if (
            package["ModelApprovalStatus"] != "Approved"
            or package.get("CustomerMetadataProperties", {}).get("artifact_sha256")
            != model["artifact_sha256"]
            or package.get("CustomerMetadataProperties", {}).get("evaluation_id")
            != model["evaluation_id"]
            or package.get("CustomerMetadataProperties", {}).get("model_key") != model_key
        ):
            raise Forbidden("Cloud deployment requires matching approved package")
        if not alarm_names:
            raise Invalid("Cloud canary requires rollback alarms")
        suffix = hashlib.sha256(model_key.encode()).hexdigest()[:16]
        name = f"aerointel-{suffix}"
        self._ensure_model(name, role, package_arn, model["artifact_sha256"])
        variants = [
            {
                "VariantName": "primary",
                "ModelName": name,
                "InitialInstanceCount": 1,
                "InstanceType": instance_type,
                "InitialVariantWeight": 1.0,
            }
        ]
        self._ensure_config(name, variants)
        deployment = {
            "BlueGreenUpdatePolicy": {
                "TrafficRoutingConfiguration": {
                    "Type": "CANARY",
                    "WaitIntervalInSeconds": 300,
                    "CanarySize": {"Type": "CAPACITY_PERCENT", "Value": 10},
                },
                "TerminationWaitInSeconds": 300,
                "MaximumExecutionTimeoutInSeconds": 3600,
            },
            "AutoRollbackConfiguration": {"Alarms": [{"AlarmName": a} for a in alarm_names]},
        }
        try:
            current = self.client.describe_endpoint(EndpointName=endpoint)
        except self.client.exceptions.ClientError as exc:
            if exc.response["Error"][
                "Code"
            ] != "ValidationException" or "Could not find" not in str(exc):
                raise
            self.client.create_endpoint(EndpointName=endpoint, EndpointConfigName=name)
        else:
            if current.get("EndpointConfigName") == name and current["EndpointStatus"] in {
                "Creating",
                "Updating",
                "InService",
                "SystemUpdating",
                "RollingBack",
            }:
                return name
            self.client.update_endpoint(
                EndpointName=endpoint, EndpointConfigName=name, DeploymentConfig=deployment
            )
        # Return submission only. Call health/status before claiming a successful deployment.
        return name

    def _ensure_model(self, name: str, role: str, package_arn: str, artifact_sha256: str) -> None:
        try:
            self.client.create_model(
                ModelName=name,
                ExecutionRoleArn=role,
                PrimaryContainer={
                    "ModelPackageName": package_arn,
                    "Environment": {"AEROINTEL_ARTIFACT_SHA256": artifact_sha256},
                },
                EnableNetworkIsolation=True,
            )
        except self.client.exceptions.ClientError as exc:
            if not self._already_exists(exc):
                raise
            current = self.client.describe_model(ModelName=name)
            if (
                current["ExecutionRoleArn"] != role
                or not current.get("EnableNetworkIsolation")
                or current["PrimaryContainer"].get("ModelPackageName") != package_arn
                or current["PrimaryContainer"]
                .get("Environment", {})
                .get("AEROINTEL_ARTIFACT_SHA256")
                != artifact_sha256
            ):
                raise Invalid("Existing cloud model has incompatible lineage or role") from exc

    def _ensure_config(self, name: str, variants: list[dict[str, Any]]) -> None:
        try:
            self.client.create_endpoint_config(EndpointConfigName=name, ProductionVariants=variants)
        except self.client.exceptions.ClientError as exc:
            if not self._already_exists(exc):
                raise
            current = self.client.describe_endpoint_config(EndpointConfigName=name)
            for actual, expected in zip(current["ProductionVariants"], variants, strict=True):
                if any(actual.get(field) != value for field, value in expected.items()):
                    raise Invalid("Existing endpoint configuration differs") from exc

    @staticmethod
    def _already_exists(exc: Any) -> bool:
        error = exc.response["Error"]
        return bool(
            error["Code"] in {"ValidationException", "ConflictException"}
            and (
                "already exist" in error["Message"].lower()
                or "already existing" in error["Message"].lower()
            )
        )

    def health(self, endpoint: str) -> bool:
        return bool(
            self.client.describe_endpoint(EndpointName=endpoint)["EndpointStatus"] == "InService"
        )


class SageMakerInference:
    def __init__(self, runtime: Any, endpoint: str) -> None:
        self.runtime, self.endpoint = runtime, endpoint

    def predict(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.runtime.invoke_endpoint(
            EndpointName=self.endpoint,
            ContentType="application/json",
            Body=json.dumps(payload, allow_nan=False).encode(),
        )
        return dict(json.loads(response["Body"].read()))
