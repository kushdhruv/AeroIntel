"""Conservative gate decision; registry authorization remains a separate boundary."""

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from contracts.validation import validate_contract


class MinimumMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    iou: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if (
        policy.get("schema_version") != "1.0.0"
        or policy.get("policy_id") != "geoleak-v1"
        or set(policy.get("minimum_metrics", {})) != {"scene", "spatial"}
    ):
        raise ValueError("Invalid quality policy identity or splits")
    for metrics in policy["minimum_metrics"].values():
        MinimumMetrics.model_validate(metrics)
    expected = {
        "required_decision": "PASS",
        "require_authorized_human_approval": True,
        "missing_spatial_evaluation": "REVIEW_REQUIRED",
        "review_required_blocks_promotion": True,
    }
    if policy.get("promotion") != expected:
        raise ValueError("Quality policy cannot bypass spatial evidence or human approval")
    return policy


@dataclass(frozen=True)
class GateDecision:
    decision: Literal["PASS", "FAIL", "REVIEW_REQUIRED"]
    reasons: tuple[str, ...]


def load_policy(path: Path | None = None) -> dict[str, Any]:
    source = path or files("specs").joinpath("policies", "quality-gates.yaml")
    return validate_policy(dict(yaml.safe_load(source.read_text(encoding="utf-8"))))


def evaluate_gates(report: dict[str, Any], policy: dict[str, Any] | None = None) -> GateDecision:
    validate_contract("evaluation-report", report)
    policy = validate_policy(policy) if policy else load_policy()
    if policy["policy_id"] != report["policy_id"]:
        return GateDecision("FAIL", ("Quality policy identity mismatch",))
    reasons = []
    for check in ("duplicate_pairs", "scene_leaks", "overlapping_tiles"):
        if report["leakage"][check] > 0:
            reasons.append(f"Leakage detected: {check}")
    for method in ("scene", "spatial"):
        result = report["splits"][method]
        if result is not None:
            for metric, threshold in policy["minimum_metrics"][method].items():
                if result[metric] < threshold:
                    reasons.append(f"{method} {metric} below {threshold}")
    if reasons:
        return GateDecision("FAIL", tuple(reasons))
    missing = [name for name in ("scene", "spatial") if report["splits"][name] is None]
    if missing:
        return GateDecision("REVIEW_REQUIRED", (f"Missing evaluation: {', '.join(missing)}",))
    if report["leakage"]["overlap_check_status"] != "completed":
        return GateDecision("REVIEW_REQUIRED", ("Tile overlap checks unavailable",))
    return GateDecision("PASS", ())
