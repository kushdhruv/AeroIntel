"""SQLite unit of work. No connection is shared across threads or requests."""

from __future__ import annotations

import builtins
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.errors import Conflict, NotFound


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents(
                    kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, logical_key TEXT UNIQUE NOT NULL,
                    state TEXT NOT NULL, stage TEXT NOT NULL, config TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', error TEXT,
                    owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS audit(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL, operation TEXT NOT NULL, target TEXT NOT NULL,
                    decision TEXT NOT NULL, reason TEXT NOT NULL, request_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox(
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS dedup(
                    scope TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(scope,key));
                CREATE TABLE IF NOT EXISTS active(
                    slot TEXT PRIMARY KEY, model_key TEXT NOT NULL, deployment_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deployments(
                    id TEXT PRIMARY KEY, model_key TEXT NOT NULL, previous_key TEXT,
                    status TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit is append only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit is append only'); END;
            """)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, kind: str, key: str, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        if db is None:
            with self.transaction() as connection:
                return self.get(kind, key, connection)
        row = db.execute("SELECT body FROM documents WHERE kind=? AND id=?", (kind, key)).fetchone()
        if row is None:
            raise NotFound(f"{kind} {key} does not exist")
        return dict(json.loads(row["body"]))

    def put(self, db: sqlite3.Connection, kind: str, key: str, body: dict[str, Any]) -> None:
        existing = db.execute(
            "SELECT body FROM documents WHERE kind=? AND id=?", (kind, key)
        ).fetchone()
        serialized = encode(body)
        if existing:
            if existing["body"] != serialized:
                raise Conflict(f"Immutable {kind} {key} already exists with different content")
            return
        db.execute("INSERT INTO documents VALUES(?,?,?)", (kind, key, serialized))

    def list(self, kind: str) -> list[dict[str, Any]]:
        with self.transaction() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT body FROM documents WHERE kind=? ORDER BY id", (kind,)
                )
            ]

    def audit(
        self,
        actor: str,
        operation: str,
        target: str,
        decision: str,
        reason: str,
        request_id: str | None = None,
        db: sqlite3.Connection | None = None,
    ) -> None:
        if db is None:
            with self.transaction() as connection:
                self.audit(actor, operation, target, decision, reason, request_id, connection)
            return
        db.execute(
            """INSERT INTO audit(timestamp,actor,operation,target,decision,reason,request_id)
            VALUES(?,?,?,?,?,?,?)""",
            (now(), actor, operation, target, decision, reason, request_id or str(uuid4())),
        )

    def history(self) -> builtins.list[dict[str, Any]]:
        with self.transaction() as db:
            return [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY seq")]

    def event(
        self,
        db: sqlite3.Connection,
        kind: str,
        payload: dict[str, Any],
        correlation: str,
        actor: str = "pipeline",
    ) -> None:
        from contracts.validation import validate_contract

        event_id = str(uuid4())
        event = {
            "schema_version": "1.0.0",
            "event_type": kind,
            "event_id": event_id,
            "occurred_at": now(),
            "correlation_id": correlation,
            "actor_id": actor,
            "payload": payload,
        }
        validate_contract(kind, event)
        db.execute("INSERT INTO outbox(id,body) VALUES(?,?)", (event_id, encode(event)))
