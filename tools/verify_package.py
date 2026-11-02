from pathlib import Path
from zipfile import ZipFile

wheel = sorted(Path("dist").glob("*.whl"), key=lambda path: path.stat().st_mtime)[-1]
with ZipFile(wheel) as archive:
    names = archive.namelist()
    assert len([name for name in names if name.endswith(".schema.json")]) == 10
    assert "specs/policies/quality-gates.yaml" in names
    assert "modules/modelops/mcp/server.py" in names
print(f"Verified packaged schemas, policies and runtime modules: {wheel.name}")
