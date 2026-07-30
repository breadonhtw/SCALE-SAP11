"""Audit-event construction helper.

Centralises the one thing every write path in the API must do: append an
AUDIT_EVENTS row with a correlation_id that ties it back to the request.
CLAUDE.md §7 — application code must never update or delete an existing
audit event; this module only ever calls `Repository.append_audit_event`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from trustsphere.domain.decisions import ActorType, AuditEvent, AuditEventType
from trustsphere.persistence.base import Repository


def record_event(
    repo: Repository,
    *,
    case_id: str,
    event_type: AuditEventType,
    actor_type: ActorType,
    actor_id: str,
    object_type: str,
    object_id: str,
    details: dict | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_id=str(uuid.uuid4()),
        case_id=case_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        object_type=object_type,
        object_id=object_id,
        details=details or {},
        occurred_at=datetime.now(timezone.utc),
        correlation_id=correlation_id or str(uuid.uuid4()),
    )
    repo.append_audit_event(event)
    return event
