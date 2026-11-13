"""
Storage layer for alarm events.
In-memory for POC; can be swapped for Supabase later without changing callers.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

SEVERITY_ORDER = ["critical", "major", "minor", "warning", "info"]

_SEVERITY_ALIASES = {
    "critical": "critical", "crit": "critical", "fatal": "critical", "emergency": "critical",
    "major": "major", "high": "major", "error": "major",
    "minor": "minor", "medium": "minor", "moderate": "minor",
    "warning": "warning", "warn": "warning", "low": "warning",
}

# Payload shapes vary between senders, so look in a few likely places.
_SOURCE_KEYS = (
    "source", "sourceName", "device", "deviceName", "host", "hostname",
    "resource", "resourceName", "node", "nodeName", "entity", "entityName",
)
_DESCRIPTION_KEYS = ("description", "message", "summary", "alarmDescription", "text", "details")


def normalize_severity(value: Any) -> str:
    if value is None:
        return "info"
    return _SEVERITY_ALIASES.get(str(value).strip().lower(), "info")


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and value != "":
            return str(value)
    return None


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def is_cleared(state: Any) -> bool:
    return str(state or "").strip().upper() == "CLEARED"


class InMemoryStore:
    """In-memory list of alarm events. No persistence across restarts."""

    def __init__(self, max_events: Optional[int] = None):
        self._events: list[dict[str, Any]] = []
        self._acks: dict[str, dict[str, str]] = {}
        self._max_events = max_events  # None = unbounded (POC)

    def add_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        raw_severity = payload.get("alarmSeverity", payload.get("severity"))
        record = {
            "id": str(uuid.uuid4()),
            "received_at": now,
            "alarm_id": payload.get("alarmId"),
            "state": payload.get("state"),
            "severity": normalize_severity(raw_severity),
            "severity_label": str(raw_severity) if raw_severity not in (None, "") else None,
            "rule": payload.get("rule"),
            "source": _first(payload, _SOURCE_KEYS),
            "description": _first(payload, _DESCRIPTION_KEYS),
            "payload": payload,
        }
        self._events.append(record)
        # A re-raise needs a fresh acknowledgement.
        if not is_cleared(record["state"]):
            self._acks.pop(str(record["alarm_id"]), None)
        if self._max_events and len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]
        return record

    def get_events(
        self,
        alarm_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        out = self._events
        if alarm_id is not None:
            out = [e for e in out if e.get("alarm_id") == alarm_id]
        if state is not None:
            out = [e for e in out if e.get("state") == state]
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
            [e for e in self._events if str(e.get("alarm_id")) == alarm_id],
            key=lambda e: e.get("received_at") or "",
        )

    def count(self) -> int:
        return len(self._events)

    # ---------- Correlated alarms (one row per alarmId) ----------

    def get_alarms(self) -> list[dict[str, Any]]:
        """Fold the event stream into one record per alarm, worst and newest first."""
        alarms: dict[str, dict[str, Any]] = {}
        for e in self._events:  # insertion order == chronological
            key = str(e.get("alarm_id"))
            a = alarms.get(key)
            if a is None:
                a = alarms[key] = {
                    "alarm_id": key,
                    "first_seen": e["received_at"],
                    "event_count": 0,
                    "raise_count": 0,
                    "raised_at": None,
                    "cleared_at": None,
                    "severity": None,
                    "severity_label": None,
                    "rule": None,
                    "source": None,
                    "description": None,
                }
            cleared = is_cleared(e.get("state"))
            was_active = a.get("active", False)
            a["event_count"] += 1
            a["last_seen"] = e["received_at"]
            a["last_event_id"] = e["id"]
            a["state"] = e.get("state")
            a["active"] = not cleared
            if cleared:
                a["cleared_at"] = e["received_at"]
            else:
                a["raise_count"] += 1
                a["cleared_at"] = None
                if not was_active:
                    a["raised_at"] = e["received_at"]
            # Clear notifications often omit detail; keep what the raise told us.
            if not cleared or a["severity"] is None:
                a["severity"] = e.get("severity") or "info"
                a["severity_label"] = e.get("severity_label")
            for field in ("rule", "source", "description"):
                if e.get(field):
                    a[field] = e[field]
        for key, a in alarms.items():
            a["ack"] = self._acks.get(key)
            start, end = _parse(a["raised_at"]), _parse(a["cleared_at"])
            a["duration_s"] = (end - start).total_seconds() if start and end else None
        return sorted(
            alarms.values(),
            key=lambda a: (
                not a["active"],
                SEVERITY_ORDER.index(a["severity"]) if a["active"] else 0,
                -(_parse(a["last_seen"]) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            ),
        )

    def get_alarm(self, alarm_id: str) -> Optional[dict[str, Any]]:
        for a in self.get_alarms():
            if a["alarm_id"] == alarm_id:
                return a
        return None

    def set_ack(self, alarm_id: str, user: Optional[str]) -> bool:
        """Acknowledge (user given) or un-acknowledge (user None). False if alarm unknown."""
        if not any(str(e.get("alarm_id")) == alarm_id for e in self._events):
            return False
        if user is None:
            self._acks.pop(alarm_id, None)
        else:
            self._acks[alarm_id] = {"by": user, "at": datetime.now(timezone.utc).isoformat()}
        return True

    def stats(self, alarms: list[dict[str, Any]], minutes: int = 60) -> dict[str, Any]:
        active = [a for a in alarms if a["active"]]
        durations = [a["duration_s"] for a in alarms if not a["active"] and a["duration_s"] is not None]
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = now - timedelta(minutes=minutes - 1)
        series = [{"t": (start + timedelta(minutes=i)).isoformat(), "raised": 0, "cleared": 0} for i in range(minutes)]
        for e in reversed(self._events):
            ts = _parse(e.get("received_at"))
            if ts is None:
                continue
            if ts < start:
                break
            idx = int((ts - start).total_seconds() // 60)
            if 0 <= idx < minutes:
                series[idx]["cleared" if is_cleared(e.get("state")) else "raised"] += 1
        return {
            "active": len(active),
            "unacked": sum(1 for a in active if not a["ack"]),
            "cleared": len(alarms) - len(active),
            "by_severity": {s: sum(1 for a in active if a["severity"] == s) for s in SEVERITY_ORDER},
            "mttr_s": sum(durations) / len(durations) if durations else None,
            "events_total": len(self._events),
            "events_window": sum(b["raised"] + b["cleared"] for b in series),
            "series": series,
        }


# Single global store for the app (in-memory POC)
store = InMemoryStore()
