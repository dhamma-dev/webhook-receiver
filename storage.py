"""
Storage layer for alarm events.
In-memory for POC; can be swapped for Supabase later without changing callers.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _payload_val(event: dict, key: str) -> Optional[str]:
    """Normalize payload value to string for filtering (orgId/connectorId may be int)."""
    p = event.get("payload") or {}
    v = p.get(key)
    return str(v) if v is not None else None


class InMemoryStore:
    """In-memory list of alarm events. No persistence across restarts."""

    def __init__(self, max_events: Optional[int] = None):
        self._events: list[dict[str, Any]] = []
        self._max_events = max_events  # None = unbounded (POC)

    def add_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": str(uuid.uuid4()),
            "received_at": now,
            "alarm_id": payload.get("alarmId"),
            "state": payload.get("state"),
            "payload": payload,
        }
        self._events.append(record)
        if self._max_events and len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]
        return record

    def get_events(
        self,
        alarm_id: Optional[str] = None,
        state: Optional[str] = None,
        org_id: Optional[str] = None,
        connector_id: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        out = self._events
        if alarm_id is not None:
            out = [e for e in out if e.get("alarm_id") == alarm_id]
        if state is not None:
            out = [e for e in out if e.get("state") == state]
        if org_id is not None:
            out = [e for e in out if _payload_val(e, "orgId") == org_id]
        if connector_id is not None:
            out = [e for e in out if _payload_val(e, "connectorId") == connector_id]
        if since is not None:
            out = [e for e in out if (e.get("received_at") or "") >= since]
        if until is not None:
            out = [e for e in out if (e.get("received_at") or "") <= until]
        # newest first
        out = sorted(out, key=lambda e: e.get("received_at") or "", reverse=True)
        return out[offset : offset + limit]

    def get_event_by_id(self, event_id: str) -> Optional[dict[str, Any]]:
        for e in self._events:
            if e.get("id") == event_id:
                return e
        return None

    def get_events_by_alarm_id(self, alarm_id: str) -> list[dict[str, Any]]:
        return sorted(
            [e for e in self._events if e.get("alarm_id") == alarm_id],
            key=lambda e: e.get("received_at") or "",
        )

    def count(self) -> int:
        return len(self._events)


# Single global store for the app (in-memory POC)
store = InMemoryStore()
