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


class PayloadInspectStore:
    """In-memory store for arbitrary POST payloads (inspect endpoint)."""

    def __init__(self, max_items: Optional[int] = 100):
        self._items: list[dict[str, Any]] = []
        self._max_items = max_items

    def add(
        self,
        *,
        content_type: Optional[str] = None,
        headers: dict[str, str],
        raw_body: str,
        parsed_body: Optional[Any] = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": str(uuid.uuid4()),
            "received_at": now,
            "content_type": content_type or "",
            "headers": headers,
            "raw_body": raw_body,
            "parsed_body": parsed_body,
        }
        self._items.append(record)
        if self._max_items and len(self._items) > self._max_items:
            self._items = self._items[-self._max_items :]
        return record

    def _matches(self, item: dict[str, Any], org_id: Optional[str] = None, type_val: Optional[str] = None) -> bool:
        """True if item's parsed_body (when dict) matches org_id and type filters."""
        if not org_id and not type_val:
            return True
        parsed = item.get("parsed_body")
        if not isinstance(parsed, dict):
            return not (org_id or type_val)  # no parsed body -> no match if any filter set
        if org_id and str(parsed.get("orgId") or "") != str(org_id):
            return False
        if type_val and str(parsed.get("type") or "") != str(type_val):
            return False
        return True

    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        org_id: Optional[str] = None,
        type_val: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        out = self._items
        if org_id or type_val:
            out = [e for e in out if self._matches(e, org_id=org_id, type_val=type_val)]
        out = sorted(out, key=lambda e: e.get("received_at") or "", reverse=True)
        return out[offset : offset + limit]

    def get_by_id(self, item_id: str) -> Optional[dict[str, Any]]:
        for e in self._items:
            if e.get("id") == item_id:
                return e
        return None

    def count(
        self,
        org_id: Optional[str] = None,
        type_val: Optional[str] = None,
    ) -> int:
        if not org_id and not type_val:
            return len(self._items)
        return sum(1 for e in self._items if self._matches(e, org_id=org_id, type_val=type_val))

    def get_all_for_export(
        self,
        org_id: Optional[str] = None,
        type_val: Optional[str] = None,
        max_items: int = 10_000,
    ) -> list[dict[str, Any]]:
        """Return all matching items (newest first) for export, up to max_items."""
        out = self._items
        if org_id or type_val:
            out = [e for e in out if self._matches(e, org_id=org_id, type_val=type_val)]
        out = sorted(out, key=lambda e: e.get("received_at") or "", reverse=True)
        return out[:max_items]


payload_inspect_store = PayloadInspectStore()
