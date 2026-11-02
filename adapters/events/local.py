"""Transactional outbox dispatch; consumers are required to be idempotent."""

import json
from collections.abc import Callable
from typing import Any

from app.core.database import Database


def dispatch(db: Database, publish: Callable[[dict[str, Any]], None]) -> int:
    with db.transaction() as connection:
        events = list(
            connection.execute("SELECT id,body FROM outbox WHERE published=0 ORDER BY rowid")
        )
    count = 0
    for event in events:
        # A crash after publish but before acknowledgement can redeliver this event.
        publish(json.loads(event["body"]))
        with db.transaction() as connection:
            connection.execute("UPDATE outbox SET published=1 WHERE id=?", (event["id"],))
        count += 1
    return count
