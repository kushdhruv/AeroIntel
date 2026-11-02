"""S3 object adapter with immutable conditional writes and SHA-256 verification."""

from pathlib import PurePosixPath
from typing import Any

from adapters.storage.local import digest
from app.core.errors import Conflict, Forbidden, Invalid


class S3Storage:
    def __init__(
        self, client: Any, bucket: str, prefix: str = "aerointel", allow_mutations: bool = False
    ) -> None:
        self.client, self.bucket = client, bucket
        self.prefix, self.allow_mutations = prefix.strip("/"), allow_mutations
        if not self.prefix:
            raise Invalid("S3 adapter requires a confined prefix")

    def key(self, key: str) -> str:
        if (
            not key
            or key.startswith("/")
            or ":" in key
            or "\\" in key
            or ".." in PurePosixPath(key).parts
        ):
            raise Invalid("Invalid S3 object key")
        return f"{self.prefix}/{key}"

    def write(self, key: str, data: bytes) -> str:
        if not self.allow_mutations:
            raise Forbidden("Cloud mutations are disabled")
        object_key = self.key(key)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=data,
                Metadata={"sha256": digest(data)},
                IfNoneMatch="*",
                ServerSideEncryption="AES256",
            )
        except self.client.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
                raise
            if self.read(key) != data:
                raise Conflict("S3 immutable object already exists") from exc
        return f"s3://{self.bucket}/{object_key}"

    def read(self, key: str, expected: str | None = None) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self.key(key))
        data = response["Body"].read()
        recorded = response.get("Metadata", {}).get("sha256")
        if not recorded or digest(data) != recorded or (expected and digest(data) != expected):
            raise Invalid("S3 checksum missing or mismatched")
        return bytes(data)
