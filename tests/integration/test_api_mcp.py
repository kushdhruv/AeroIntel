import asyncio
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.main import create_app
from app.core.errors import Forbidden
from app.core.platform import Platform
from app.core.security import Principal
from modules.modelops.mcp.server import create_server


def test_api_auth_and_approval_security(trained: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    identities = {
        hashlib.sha256(token.encode()).hexdigest(): value
        for token, value in {
            "reader-test": {"id": "reader", "role": "reader"},
            "operator-test": {"id": "operator", "role": "operator"},
            "human-test": {"id": "human", "role": "approver", "human": True},
        }.items()
    }
    monkeypatch.setenv("AEROINTEL_IDENTITIES", json.dumps(identities))
    client = TestClient(create_app(trained))
    assert client.get("/health").status_code == 200
    assert client.get("/models").status_code == 403
    model = trained.db.list("model")[0]
    key = f"{model['model_id']}:{model['model_version']}"
    headers = {"Authorization": "Bearer operator-test"}
    assert (
        client.post(
            f"/models/{key}/approve", headers=headers, json={"request_id": "bad"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/models/{key}/deploy", headers=headers, json={"request_id": "no-approval"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/models/{key}/approve",
            headers={"Authorization": "Bearer human-test"},
            json={"request_id": "human-approved"},
        ).status_code
        == 200
    )
    assert (
        client.post(f"/models/{key}/deploy", headers=headers, json={"request_id": "deploy"}).json()[
            "status"
        ]
        == "active"
    )
    assert (
        client.post(
            "/modelops/request_model_promotion",
            headers=headers,
            json={"arguments": {"target": key, "request_id": "a", "role": "approver"}},
        ).status_code
        == 422
    )
    assert client.get("/audit", headers={"Authorization": "Bearer reader-test"}).status_code == 200


def test_mcp_discovery_raw_validation_and_denial_audit(trained: Platform) -> None:
    server = create_server(trained, Principal("agent", "reader"))

    async def exercise() -> None:
        tools = await server.list_tools()
        assert {tool.name for tool in tools} == {
            "get_pipeline_status",
            "list_model_versions",
            "get_geoleak_report",
            "get_drift_report",
            "create_retraining_proposal",
            "request_model_promotion",
        }
        result = await server.call_tool("list_model_versions", {})
        assert result
        with pytest.raises(ValidationError):
            await server.call_tool("get_pipeline_status", {"target": "x", "role": "approver"})
        with pytest.raises(Forbidden):
            await server.call_tool(
                "request_model_promotion", {"target": "x", "request_id": "write"}
            )

    asyncio.run(exercise())
    assert [event["decision"] for event in trained.db.history()[-2:]] == ["denied", "denied"]


def test_body_limit_rejects_before_json_decode(trained: Platform) -> None:
    from app.api.limits import BodyLimit

    api = create_app(trained)
    api.add_middleware(BodyLimit, limit=20)
    client = TestClient(api)
    assert (
        client.post(
            "/predictions", content=b"x" * 21, headers={"content-type": "application/json"}
        ).status_code
        == 413
    )
