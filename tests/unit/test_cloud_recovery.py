from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from adapters.training.sagemaker import SageMaker
from app.core.errors import Invalid


class RecoverableClient:
    exceptions = SimpleNamespace(ClientError=ClientError)

    def __init__(self, wrong_role: bool = False) -> None:
        self.wrong_role = wrong_role

    def create_model(self, **kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "Cannot create already existing model",
                }
            },
            "CreateModel",
        )

    def describe_model(self, **kwargs):
        return {
            "ExecutionRoleArn": "wrong" if self.wrong_role else "role",
            "PrimaryContainer": {
                "ModelPackageName": "package",
                "Environment": {"AEROINTEL_ARTIFACT_SHA256": "digest"},
            },
            "EnableNetworkIsolation": True,
        }

    def create_endpoint_config(self, **kwargs):
        raise ClientError(
            {"Error": {"Code": "ValidationException", "Message": "Already exists"}},
            "CreateEndpointConfig",
        )

    def describe_endpoint_config(self, **kwargs):
        return {"ProductionVariants": [{"ModelName": "model", "InitialInstanceCount": 1}]}


def test_cloud_retry_reuses_only_matching_model_and_endpoint_config() -> None:
    adapter = SageMaker(RecoverableClient(), None, True)
    adapter._ensure_model("model", "role", "package", "digest")
    adapter._ensure_config("config", [{"ModelName": "model", "InitialInstanceCount": 1}])
    with pytest.raises(Invalid):
        SageMaker(RecoverableClient(True), None, True)._ensure_model(
            "model", "role", "package", "digest"
        )
    with pytest.raises(Invalid):
        adapter._ensure_config("config", [{"ModelName": "unexpected"}])
