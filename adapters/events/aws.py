import json
from typing import Any

from app.core.errors import Forbidden, Invalid
from contracts.validation import validate_contract


class EventBridge:
    def __init__(self, client: Any, bus: str, allow_mutations: bool = False) -> None:
        self.client, self.bus, self.allow_mutations = client, bus, allow_mutations

    def publish(self, event: dict[str, Any]) -> None:
        validate_contract(event["event_type"], event)
        if not self.allow_mutations:
            raise Forbidden("Cloud mutations are disabled")
        response = self.client.put_events(
            Entries=[
                {
                    "Source": "aerointel.platform",
                    "DetailType": event["event_type"],
                    "Detail": json.dumps(event, allow_nan=False),
                    "EventBusName": self.bus,
                }
            ]
        )
        if response.get("FailedEntryCount", 0):
            raise Invalid("EventBridge rejected an event; outbox must retain it for retry")
