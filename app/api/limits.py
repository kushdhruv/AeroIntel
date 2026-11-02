"""Bound request bodies before JSON parsing, including chunked HTTP requests."""

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413, "Request exceeds configured body limit")


class BodyLimit:
    def __init__(self, app: ASGIApp, limit: int = 32_000_000) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        size = 0

        async def bounded() -> Message:
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.limit:
                raise BodyTooLarge()
            return message

        await self.app(scope, bounded, send)
