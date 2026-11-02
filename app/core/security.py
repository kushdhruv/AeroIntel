"""Server-configured identities; request payloads cannot supply their own roles."""

import hashlib
import hmac
import json
import os
from dataclasses import dataclass

from app.core.errors import Forbidden, Invalid


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    human: bool = False


def authenticate(token: str) -> Principal:
    # Map SHA256(token) to identity. No token or identity values are logged.
    configured = json.loads(os.environ.get("AEROINTEL_IDENTITIES", "{}"))
    checksum = hashlib.sha256(token.encode()).hexdigest()
    for key, identity in configured.items():
        if hmac.compare_digest(checksum, key):
            principal = Principal(**identity)
            if principal.role not in {"reader", "operator", "approver"}:
                raise Invalid("Invalid server identity configuration")
            return principal
    raise Forbidden("Authentication required")


def require(principal: Principal, role: str, human: bool = False) -> None:
    allowed = {
        "reader": {"reader", "operator", "approver"},
        "operator": {"operator"},
        "approver": {"approver"},
    }
    if principal.role not in allowed.get(role, set()) or (human and not principal.human):
        raise Forbidden(f"Operation requires {'human ' if human else ''}{role}")
