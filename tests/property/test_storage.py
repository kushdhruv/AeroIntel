from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adapters.storage.local import LocalStorage, digest
from app.core.errors import Conflict, Invalid


@given(st.binary(min_size=0, max_size=1000))
def test_content_addressed_writes_are_idempotent(data: bytes) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        storage = LocalStorage(Path(temporary))
        key = digest(data)[7:]
        storage.write(key, data)
        storage.write(key, data)
        assert storage.read(key, digest(data)) == data
        with pytest.raises(Conflict):
            storage.write(key, data + b"different")


@pytest.mark.parametrize("key", ["../../secret", "C:/secret", "a\\b", "/outside", ""])
def test_path_escape_blocked(tmp_path: Path, key: str) -> None:
    with pytest.raises(Invalid):
        LocalStorage(tmp_path / "store").write(key, b"no")
