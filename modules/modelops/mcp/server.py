"""Local stdio MCP transport. No LLM provider or AWS credential is needed."""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from app.core.database import encode
from app.core.platform import Platform
from app.core.security import Principal, authenticate
from modules.modelops.service import ModelOps


def create_server(platform: Platform | None = None, principal: Principal | None = None) -> FastMCP:
    platform = platform or Platform()
    if principal is None:
        token = os.environ.get("AEROINTEL_MCP_TOKEN")
        authenticated = authenticate(token) if token else Principal("mcp-local", "reader")
        # An agent transport cannot assert human presence, even with an approver credential.
        principal = Principal(authenticated.id, authenticated.role, human=False)
    operations = ModelOps(platform)
    actor = principal

    class AuditedMCP(FastMCP):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            # Enforce and audit raw arguments before the SDK's coercive wrapper validation.
            result = operations.call(name, arguments, actor)
            return [TextContent(type="text", text=encode(result))]

    server = AuditedMCP("AeroIntel ModelOps")

    @server.tool()
    def get_pipeline_status(target: str) -> Any:
        return operations.call("get_pipeline_status", {"target": target}, actor)

    @server.tool()
    def list_model_versions() -> Any:
        return operations.call("list_model_versions", {}, actor)

    @server.tool()
    def get_geoleak_report(target: str) -> Any:
        return operations.call("get_geoleak_report", {"target": target}, actor)

    @server.tool()
    def get_drift_report(target: str) -> Any:
        return operations.call("get_drift_report", {"target": target}, actor)

    @server.tool()
    def create_retraining_proposal(target: str, request_id: str, drift_report_id: str) -> Any:
        return operations.call(
            "create_retraining_proposal",
            {
                "target": target,
                "request_id": request_id,
                "drift_report_id": drift_report_id,
            },
            actor,
        )

    @server.tool()
    def request_model_promotion(target: str, request_id: str) -> Any:
        return operations.call(
            "request_model_promotion",
            {
                "target": target,
                "request_id": request_id,
            },
            actor,
        )

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
