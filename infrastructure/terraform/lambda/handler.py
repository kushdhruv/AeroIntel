"""Narrow event-to-pipeline trigger. No approval, deployment or arbitrary API surface."""

import hashlib
import json
import os
from typing import Any


def handler(event: dict[str, Any], context: Any, client: Any = None) -> dict[str, Any]:
    if os.environ.get("CLOUD_EXECUTION_ENABLED", "false") != "true":
        return {"status": "disabled"}
    detail = event["detail"]
    if (
        detail.get("event_type") != "dataset-batch-completed"
        or detail.get("schema_version") != "1.0.0"
    ):
        raise ValueError("Unsupported dataset event")
    payload = detail["payload"]
    if not payload["manifest_uri"].startswith("s3://"):
        raise ValueError("Cloud trigger requires S3 manifest URI")
    # Logical identity survives redelivery with a different EventBridge event ID.
    token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if client is None:
        import boto3

        client = boto3.client("sagemaker")
    result = client.start_pipeline_execution(
        PipelineName=os.environ["PIPELINE_NAME"],
        ClientRequestToken=token,
        PipelineParameters=[{"Name": "ManifestUri", "Value": payload["manifest_uri"]}],
    )
    return {"execution_arn": result["PipelineExecutionArn"]}
