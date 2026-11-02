"""Authenticated local API over the same domain services used by CLI and MCP."""

import base64
import binascii
from functools import lru_cache
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jsonschema import ValidationError as SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.limits import BodyLimit, BodyTooLarge
from app.core.errors import Conflict, Forbidden, Invalid, NotFound, PlatformError
from app.core.platform import Platform
from app.core.security import Principal, authenticate, require
from modules.modelops.service import ModelOps
from modules.propertypulse.training.engine import TrainingConfig


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestBody(Body):
    manifest: dict[str, Any]
    files: dict[str, str] = Field(max_length=300)


class RunBody(Body):
    dataset_key: str = Field(min_length=1, max_length=200)
    config: TrainingConfig = Field(default_factory=TrainingConfig)


class PredictionBody(Body):
    before: str = Field(max_length=12_000_000)
    after: str = Field(max_length=12_000_000)
    region: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
    metadata: dict[str, Any] | None = None


class FeedbackBody(Body):
    mask: str = Field(max_length=12_000_000)


class OperationBody(Body):
    request_id: str = Field(min_length=1, max_length=200)


class MonitorBody(Body):
    model_key: str
    start: str
    end: str


class ToolBody(Body):
    arguments: dict[str, Any]


def raw(value: str) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Invalid("Invalid base64 image") from exc
    if len(data) > 8_000_000:
        raise Invalid("Image file exceeds 8MB")
    return data


def create_app(platform: Platform | None = None) -> FastAPI:
    api = FastAPI(title="AeroIntel", version="1.0.0")
    api.add_middleware(BodyLimit)

    @api.exception_handler(BodyTooLarge)
    async def oversized(request: Request, exc: BodyTooLarge) -> JSONResponse:
        return JSONResponse(status_code=413, content={"error": "Request exceeds 32MB"})

    @lru_cache
    def get_platform() -> Platform:
        return platform or Platform()

    def identity(authorization: Annotated[str | None, Header()] = None) -> Principal:
        try:
            if not authorization or not authorization.startswith("Bearer "):
                raise Forbidden("Bearer authentication required")
            return authenticate(authorization[7:])
        except Forbidden:
            get_platform().db.audit(
                "anonymous", "authenticate", "api", "denied", "Invalid credentials"
            )
            raise

    Actor = Annotated[Principal, Depends(identity)]

    def permit(actor: Principal, role: str, operation: str) -> None:
        try:
            require(actor, role)
        except Forbidden:
            get_platform().db.audit(actor.id, operation, "api", "denied", "Insufficient role")
            raise

    @api.exception_handler(PlatformError)
    async def domain_error(request: Request, exc: PlatformError) -> JSONResponse:
        code = (
            403
            if isinstance(exc, Forbidden)
            else 409
            if isinstance(exc, Conflict)
            else (404 if isinstance(exc, NotFound) else 422)
        )
        return JSONResponse(
            status_code=code, content={"error": type(exc).__name__, "detail": str(exc)}
        )

    @api.exception_handler(SchemaError)
    async def schema_error(request: Request, exc: SchemaError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"error": "Invalid contract", "detail": exc.message}
        )

    @api.exception_handler(RequestValidationError)
    async def request_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo raw image contents, tokens or extra argument values into responses/logs.
        get_platform().db.audit(
            "api-client", "validate_request", request.url.path, "denied", "Invalid arguments"
        )
        return JSONResponse(status_code=422, content={"error": "Invalid request arguments"})

    @api.exception_handler(ValidationError)
    async def validation_error(request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "Invalid operation arguments"})

    @api.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "contract_version": "1.0.0"}

    @api.post("/datasets", tags=["ingestion"])
    def ingest(body: IngestBody, actor: Actor) -> dict[str, Any]:
        permit(actor, "operator", "ingest")
        service = get_platform()
        from adapters.storage.local import LocalStorage

        staging = LocalStorage(service.root / "incoming" / str(uuid4()))
        for key, value in body.files.items():
            staging.write(key, raw(value))
        return service.ingestion.register(body.manifest, staging.root)

    @api.post("/pipelines", tags=["pipeline"])
    def submit(body: RunBody, actor: Actor) -> dict[str, str]:
        permit(actor, "operator", "submit_pipeline")
        return {"run_id": get_platform().pipeline.submit(body.dataset_key, body.config)}

    @api.post("/pipelines/{run_id}/execute", tags=["pipeline"])
    def execute(run_id: str, actor: Actor) -> dict[str, Any]:
        permit(actor, "operator", "execute_pipeline")
        return get_platform().pipeline.execute(run_id)

    @api.get("/pipelines/{run_id}", tags=["pipeline"])
    def status(run_id: str, actor: Actor) -> dict[str, Any]:
        permit(actor, "reader", "read_pipeline")
        return get_platform().pipeline.status(run_id)

    @api.get("/models", tags=["registry"])
    def versions(actor: Actor) -> list[dict[str, Any]]:
        permit(actor, "reader", "list_models")
        return get_platform().registry.versions()

    @api.post("/models/{key}/approve", tags=["registry"])
    def approve(key: str, body: OperationBody, actor: Actor) -> dict[str, Any]:
        return get_platform().registry.approve(key, actor, body.request_id)

    @api.post("/models/{key}/deploy", tags=["deployment"])
    def deploy(key: str, body: OperationBody, actor: Actor) -> dict[str, Any]:
        return get_platform().deployment.deploy(key, actor, body.request_id)

    @api.post("/deployments/rollback", tags=["deployment"])
    def rollback(body: OperationBody, actor: Actor) -> dict[str, Any]:
        permit(actor, "operator", "rollback")
        return get_platform().deployment.rollback(actor, body.request_id)

    @api.get("/deployments", tags=["deployment"])
    def deployments(actor: Actor) -> list[dict[str, Any]]:
        permit(actor, "reader", "read_deployments")
        return get_platform().deployment.history()

    @api.post("/predictions", tags=["inference"])
    def inference(body: PredictionBody, actor: Actor) -> dict[str, Any]:
        permit(actor, "reader", "predict")
        output = get_platform().production.infer(
            raw(body.before), raw(body.after), body.region, body.request_id, body.metadata
        )
        return {
            **output,
            "mask": output["mask"].tolist(),
            "probability_mask": output["probability_mask"].tolist(),
        }

    @api.post("/predictions/{prediction_id}/feedback", tags=["monitoring"])
    def feedback(prediction_id: str, body: FeedbackBody, actor: Actor) -> dict[str, str]:
        get_platform().production.feedback(prediction_id, raw(body.mask), actor)
        return {"status": "recorded"}

    @api.post("/monitoring", tags=["monitoring"])
    def monitor(body: MonitorBody, actor: Actor) -> dict[str, Any]:
        permit(actor, "operator", "monitor")
        return get_platform().monitoring.run(body.model_key, body.start, body.end)

    @api.get("/reports/{kind}/{key}", tags=["reports"])
    def report(kind: str, key: str, actor: Actor) -> dict[str, Any]:
        permit(actor, "reader", "read_report")
        if kind not in {"evaluation", "drift", "lineage", "drift-evidence"}:
            raise NotFound("Unknown report kind")
        return get_platform().db.get(kind, key)

    @api.get("/audit", tags=["audit"])
    def audit(actor: Actor) -> list[dict[str, Any]]:
        permit(actor, "reader", "read_audit")
        return get_platform().db.history()

    @api.post("/modelops/{tool}", tags=["modelops"])
    def modelops(tool: str, body: ToolBody, actor: Actor) -> Any:
        return ModelOps(get_platform()).call(tool, body.arguments, actor)

    return api


app = create_app()
