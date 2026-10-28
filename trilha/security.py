import hashlib
import json
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    role: str


def configured_credentials() -> dict[str, Principal]:
    """Only token hashes are configured; no token or tenant comes from request JSON."""
    raw = json.loads(os.environ.get("TRILHA_CREDENTIALS", "{}"))
    if not raw:
        raise RuntimeError("TRILHA_CREDENTIALS_required")
    credentials = {}
    for digest, value in raw.items():
        if len(digest) != 64 or value["role"] not in {"reader", "operator", "reviewer"}:
            raise RuntimeError("invalid_credentials_configuration")
        credentials[digest] = Principal(value["tenant_id"], value["role"])
    return credentials


def principal(request: Request, authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "credentials_required", headers={"WWW-Authenticate": "Bearer"})
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    actor = request.app.state.credentials.get(digest)
    if actor is None:
        raise HTTPException(401, "invalid_credentials")
    return actor


def allow(actor: Principal, *roles: str):
    if actor.role not in roles:
        raise HTTPException(403, "role_not_allowed")
