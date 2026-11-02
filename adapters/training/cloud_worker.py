"""SageMaker Processing entrypoint reusing the exact local state machine.

Run only inside an explicitly authorized cloud job. The job never approves or deploys.
"""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from adapters.storage.local import LocalStorage
from app.core.platform import Platform
from modules.propertypulse.training.engine import TrainingConfig


def run(
    manifest_uri: str, s3: Any, bucket: str, root: Path = Path("/opt/ml/processing/output")
) -> dict[str, Any]:
    parsed = urlparse(manifest_uri)
    manifest_key = parsed.path.lstrip("/")
    if (
        parsed.scheme != "s3"
        or parsed.netloc != bucket
        or not manifest_key.startswith("aerointel/input/")
    ):
        raise ValueError("Manifest must reside in the configured bucket under aerointel/input/")
    manifest = json.loads(s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read(4_000_001))
    incoming = LocalStorage(root / "incoming")
    parent = str(PurePosixPath(manifest_key).parent)
    for sample in manifest["samples"]:
        for key in [*sample["image_pair"].values(), sample["mask"]]:
            incoming.path(key)  # Validate before constructing or fetching an S3 key.
            raw = s3.get_object(Bucket=bucket, Key=f"{parent}/{key}")["Body"].read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("Input object exceeds 8MB")
            incoming.write(key, raw)
    platform = Platform(root / "platform")
    platform.ingestion.register(manifest, incoming.root)
    run_id = platform.pipeline.submit(
        f"{manifest['dataset_id']}:{manifest['dataset_version']}", TrainingConfig()
    )
    status = platform.pipeline.execute(run_id)
    LocalStorage(root).write("summary.json", json.dumps(status, allow_nan=False).encode())
    return status


def main() -> None:
    import boto3

    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-uri", required=True)
    args = parser.parse_args()
    run(args.manifest_uri, boto3.client("s3"), os.environ["AEROINTEL_BUCKET"])


if __name__ == "__main__":
    main()
