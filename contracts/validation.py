"""Validate shared contracts from the single, packaged schema source."""

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

KINDS = {
    "dataset-manifest": "models",
    "model-manifest": "models",
    "evaluation-report": "models",
    "drift-report": "models",
    "dataset-batch-completed": "events",
    "training-completed": "events",
    "evaluation-completed": "events",
    "model-approved": "events",
    "drift-detected": "events",
    "retraining-proposed": "events",
}


@lru_cache
def validator(kind: str) -> Draft202012Validator:
    if kind not in KINDS:
        raise ValueError(f"Unknown contract: {kind}")
    path = files("specs").joinpath(KINDS[kind], f"{kind}.schema.json")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_contract(kind: str, document: dict[str, Any]) -> None:
    """Reject invalid documents, including unknown versions and malformed dates."""
    from jsonschema import ValidationError

    try:
        json.dumps(document, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Contracts must contain finite JSON values") from exc
    validator(kind).validate(document)
