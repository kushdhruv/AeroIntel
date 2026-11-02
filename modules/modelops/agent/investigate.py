"""Deterministic incident playbook: useful locally without an external LLM."""

from typing import Any

from app.core.platform import Platform


def investigate(platform: Platform, report_id: str) -> dict[str, Any]:
    report = platform.db.get("drift", report_id)
    evidence = platform.db.get("drift-evidence", report_id)
    findings = []
    if report["input_shift"] == "detected":
        findings.append(
            "Input feature distribution changed; inspect source, sensor and preprocessing"
        )
    if report["performance"]["status"] == "missing_labels":
        findings.append("No labels: performance degradation cannot be established")
    if report["performance"]["status"] == "degraded":
        findings.append("Labeled performance fell below the baseline tolerance")
    return {
        "report_id": report_id,
        "findings": findings,
        "evidence": evidence,
        "recommended_action": "propose_retraining"
        if report["investigation_required"]
        else "observe",
        "operations_executed": [],
    }
