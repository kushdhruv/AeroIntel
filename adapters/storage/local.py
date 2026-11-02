"""Content integrity, path confinement and atomic local object writes."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from app.core.errors import Conflict, Invalid


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        if not key or "\\" in key or ":" in key:
            raise Invalid("Storage keys must be relative POSIX paths")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise Invalid("Storage path escapes configured root")
        return path

    def write(self, key: str, data: bytes, immutable: bool = True) -> str:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Short names also work under long Windows test and worktree roots.
        temporary = path.with_name(f".tmp-{uuid4().hex[:16]}")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if immutable:
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if file_digest(path) != digest(data):
                        raise Conflict(f"Immutable object changed: {key}") from None
            else:
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return key

    def read(self, key: str, expected: str | None = None) -> bytes:
        try:
            data = self.path(key).read_bytes()
        except FileNotFoundError as exc:
            raise Invalid(f"Object missing: {key}") from exc
        if expected and digest(data) != expected:
            raise Invalid(f"Checksum mismatch: {key}")
        return data
