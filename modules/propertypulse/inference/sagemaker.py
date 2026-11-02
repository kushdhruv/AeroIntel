"""SageMaker container contract: /ping and /invocations on port 8080."""

import base64
import binascii
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from adapters.storage.local import file_digest
from app.core.errors import Invalid
from modules.ingestion.service import decode_image
from modules.propertypulse.inference.predict import predict
from modules.propertypulse.training.engine import load_model

app = FastAPI(title="AeroIntel SageMaker inference")


@lru_cache
def loaded() -> Any:
    path = Path(os.environ.get("SM_MODEL_DIR", "/opt/ml/model")) / "model.pt"
    expected = os.environ.get("AEROINTEL_ARTIFACT_SHA256")
    if not expected or file_digest(path) != expected:
        raise Invalid("Cloud artifact checksum does not match approved model")
    return load_model(path)


@app.exception_handler(Invalid)
async def invalid_request(request: Any, exc: Invalid) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": str(exc)})


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    before: str = Field(max_length=12_000_000)
    after: str = Field(max_length=12_000_000)


@app.get("/ping")
def health() -> dict[str, str]:
    loaded()
    return {"status": "ok"}


@app.post("/invocations")
def invoke(payload: Payload) -> dict[str, Any]:
    network, config = loaded()
    try:
        before = decode_image(base64.b64decode(payload.before, validate=True))
        after = decode_image(base64.b64decode(payload.after, validate=True))
    except (ValueError, binascii.Error) as exc:
        raise Invalid("Invalid encoded image") from exc
    output = predict(network, before, after, config.threshold)
    return {
        **output,
        "mask": output["mask"].tolist(),
        "probability_mask": output["probability_mask"].tolist(),
    }
