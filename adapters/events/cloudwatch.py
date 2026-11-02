from typing import Any

from app.core.errors import Forbidden


class CloudWatch:
    def __init__(self, client: Any, allow_mutations: bool = False) -> None:
        self.client, self.allow_mutations = client, allow_mutations

    def publish(self, report: dict[str, Any]) -> None:
        if not self.allow_mutations:
            raise Forbidden("Cloud metrics are disabled")
        metrics = [
            {"MetricName": "InputDrift", "Value": float(report["input_shift"] == "detected")}
        ]
        if report["performance"]["iou"] is not None:
            metrics.append({"MetricName": "IoU", "Value": report["performance"]["iou"]})
        self.client.put_metric_data(Namespace="AeroIntel", MetricData=metrics)
