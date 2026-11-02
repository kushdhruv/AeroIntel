import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.core.platform import Platform


def test_real_stdio_mcp_roundtrip_and_denied_write(tmp_path: Path) -> None:
    root = tmp_path / "mcp"

    async def exercise() -> None:
        environment = {**os.environ, "AEROINTEL_HOME": str(root), "DO_NOT_TRACK": "1"}
        environment.pop("AEROINTEL_MCP_TOKEN", None)
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "modules.modelops.mcp.server"], env=environment
        )
        async with asyncio.timeout(60), stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 6
                result = await session.call_tool("list_model_versions", {})
                assert result.isError is False
                denied = await session.call_tool(
                    "request_model_promotion",
                    {"target": "not-approved", "request_id": "transport-denied"},
                )
                assert denied.isError is True

    asyncio.run(exercise())
    assert Platform(root).db.history()[-1]["decision"] == "denied"
