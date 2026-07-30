"""Idempotency-key helper for create/start endpoints (CLAUDE.md §13).

Usage in a router:

    key = request.headers.get("Idempotency-Key")
    if key:
        req_hash = compute_request_hash(payload)
        cached = check_idempotency(repo, key, endpoint, req_hash)
        if cached is not None:
            return cached
    ... do the work, build `response` ...
    if key:
        repo.store_idempotent_response(key, endpoint, req_hash, response)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from trustsphere.api.errors import ConflictError
from trustsphere.persistence.base import Repository


def compute_request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_idempotency(repo: Repository, key: str, endpoint: str, request_hash: str) -> dict[str, Any] | None:
    """Wraps `Repository.check_and_store_idempotency_key`, converting the
    "same key reused with a different request body" case into a stable 409
    (CLAUDE.md §13 "Return stable error codes ... never let a vendor/DB
    exception leak raw") instead of falling through to the generic 500
    handler.
    """
    try:
        return repo.check_and_store_idempotency_key(key, endpoint, request_hash)
    except ValueError as e:
        raise ConflictError(str(e))
