"""
Bridge: transform incoming alarm webhooks and forward them to Slack and Google Chat.

Each destination is an "incoming webhook" URL (the URL itself is the credential).
Config can be seeded from env vars (SLACK_WEBHOOK_URL, GCHAT_WEBHOOK_URL) and changed
from the console; console changes are in memory and reset on restart.
"""
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DESTINATIONS = {
    "slack": {
        "label": "Slack",
        "env": "SLACK_WEBHOOK_URL",
        "hosts": ("hooks.slack.com", "hooks.slack-gov.com"),
        "placeholder": "https://hooks.slack.com/services/T…/B…/…",
    },
    "gchat": {
        "label": "Google Chat",
        "env": "GCHAT_WEBHOOK_URL",
        "hosts": ("chat.googleapis.com",),
        "placeholder": "https://chat.googleapis.com/v1/spaces/…/messages?key=…&token=…",
    },
}

SEVERITY_STYLE = {
    "critical": ("🔴", "#e5394f"),
    "major": ("🟠", "#f08a3c"),
    "minor": ("🟡", "#e3b93a"),
    "warning": ("🔵", "#3fb4dc"),
}
CLEARED_STYLE = ("✅", "#2fb67c")
DEFAULT_STYLE = ("⚪", "#8b95a9")

SAMPLE_PAYLOAD = {
    "alarmId": "0a998e02-ed9a-4a02-a017-23d0f5250d24",
    "alarmSeverity": "MAJOR",
    "alarmType": "QUALITY_OF_SERVICE",
    "description": "Data Loss Any (default) is greater than 0 % for 1 out of 2 samples",
    "firstViolationTime": "2026-11-13T21:13:38.097+0000",
    "itemName": "P18A06098 - ANN <-> www.politico.com (single)",
    "itemType": "NETWORK_PATH",
    "monitoringPointName": "P18A06098 - ANN",
    "monitoringPointType": "r90 rackAppliance",
    "raisedTime": "2026-11-13T21:15:51.065+0000",
    "rule": "Data loss on critical paths",
    "silenced": False,
    "state": "RAISED",
    "tags": [{"category": "Department", "value": "QA"}, {"category": "Application", "value": "Google"}],
    "target": "www.politico.com",
}


# ---------- Field helpers ----------

_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://)[^)]*\)")


def _clean(value: Any) -> str:
    """String value with any markdown-style [text](url) collapsed to text."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    return _MD_LINK.sub(r"\1", str(value)).strip()


def _humanize(value: Any) -> str:
    text = _clean(value).replace("_", " ").lower()
    return text[:1].upper() + text[1:]


def _parse_time(value: Any) -> Optional[datetime]:
    text = _clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text.replace("Z", "+0000"), fmt)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%b %-d, %H:%M:%S UTC") if os.name != "nt" else dt.astimezone(timezone.utc).strftime("%b %d, %H:%M:%S UTC")


def _duration(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60:02d}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"


def _safe_link(value: Any) -> Optional[str]:
    text = str(value).strip() if isinstance(value, str) else ""
    parts = urlsplit(text)
    return text if parts.scheme in ("http", "https") and parts.netloc else None


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the display facts out of an alarm payload, shared by both transforms."""
    cleared = str(payload.get("state") or "").strip().upper() == "CLEARED"
    severity = _clean(payload.get("alarmSeverity", payload.get("severity"))).upper()
    emoji, color = CLEARED_STYLE if cleared else SEVERITY_STYLE.get(severity.lower(), DEFAULT_STYLE)
    raised, cleared_at = _parse_time(payload.get("raisedTime")), _parse_time(payload.get("clearedTime"))
    first = _parse_time(payload.get("firstViolationTime"))
    rule = _clean(payload.get("rule")) or "Alarm"
    if cleared:
        headline = "Alarm cleared" + (f" · was {severity}" if severity else "")
    else:
        headline = (f"{severity} alarm raised" if severity else "Alarm raised")

    mp = _clean(payload.get("monitoringPointName"))
    mp_type = _clean(payload.get("monitoringPointType"))
    kind = " · ".join(x for x in (_humanize(payload.get("alarmType")), _humanize(payload.get("itemType"))) if x)
    source = _clean(payload.get("source") or payload.get("device") or payload.get("host"))

    facts: list[tuple[str, str, Optional[datetime]]] = []  # (label, text, time)
    def add(label, text, when=None):
        if text or when:
            facts.append((label, text, when))
    add("Path", _clean(payload.get("itemName")))
    add("Monitoring point", f"{mp} ({mp_type})" if mp and mp_type else mp)
    add("Target", _clean(payload.get("target")))
    add("Source", source)
    add("Type", kind)
    if not cleared:
        add("First violation", "", first)
    add("Raised", "", raised)
    if cleared:
        add("Cleared", "", cleared_at)
        if raised and cleared_at:
            add("Duration", _duration((cleared_at - raised).total_seconds()))

    tags = []
    for t in payload.get("tags") or []:
        if isinstance(t, dict) and t.get("category") is not None:
            tags.append(f"{_clean(t.get('category'))}: {_clean(t.get('value'))}")
    return {
        "cleared": cleared, "emoji": emoji, "color": color, "headline": headline, "rule": rule,
        "description": _clean(payload.get("description")), "link": _safe_link(payload.get("providerLink")),
        "facts": facts, "tags": tags, "silenced": payload.get("silenced") is True,
        "alarm_id": _clean(payload.get("alarmId")),
    }


# ---------- Slack ----------

def _slack_esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_slack(payload: dict[str, Any]) -> dict[str, Any]:
    """Slack incoming-webhook message: colour bar by severity, Block Kit body."""
    s = summarize(payload)
    rule = _slack_esc(s["rule"])
    title = f"*<{s['link']}|{rule}>*" if s["link"] else f"*{rule}*"
    lines = [f"{s['emoji']}  *{_slack_esc(s['headline'])}*", title]
    if s["description"]:
        lines.append(_slack_esc(s["description"]))
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)[:2900]}}]

    fields = []
    for label, text, when in s["facts"][:10]:
        if when:
            fallback = _utc(when)
            value = f"<!date^{int(when.timestamp())}^{{date_short_pretty}} at {{time_secs}}|{fallback}>"
        else:
            value = _slack_esc(text)[:1900]
        fields.append({"type": "mrkdwn", "text": f"*{label}*\n{value}"})
    if fields:
        blocks.append({"type": "section", "fields": fields})

    context = []
    if s["tags"]:
        shown = "  ".join(f"`{_slack_esc(t)}`" for t in s["tags"][:12])
        more = f"  +{len(s['tags']) - 12} more" if len(s["tags"]) > 12 else ""
        context.append(f"🏷️ {shown}{more}")
    footer = []
    if s["silenced"]:
        footer.append("🔕 Silenced")
    if s["link"]:
        footer.append(f"<{s['link']}|Open alarm>")
    if s["alarm_id"]:
        footer.append(f"ID `{_slack_esc(s['alarm_id'])}`")
    if footer:
        context.append("  ·  ".join(footer))
    for text in context:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": text[:2900]}]})

    return {
        "text": f"{s['emoji']} {s['headline']}: {s['rule']}",  # notification / fallback text
        "attachments": [{"color": s["color"], "blocks": blocks}],
    }


# ---------- Google Chat ----------

def to_gchat(payload: dict[str, Any]) -> dict[str, Any]:
    """Google Chat card. RAISED and CLEARED for one alarm share a thread (keyed by alarmId)."""
    s = summarize(payload)
    esc = html.escape
    widgets: list[dict[str, Any]] = []
    for label, text, when in s["facts"]:
        widgets.append({"decoratedText": {"topLabel": label, "text": esc(_utc(when) if when else text), "wrapText": True}})
    sections: list[dict[str, Any]] = []
    if s["description"]:
        sections.append({"widgets": [{"textParagraph": {"text": esc(s["description"])}}]})
    if widgets:
        sections.append({"widgets": widgets})
    extra = []
    if s["tags"]:
        shown = ", ".join(esc(t) for t in s["tags"][:12])
        more = f" +{len(s['tags']) - 12} more" if len(s["tags"]) > 12 else ""
        extra.append({"decoratedText": {"topLabel": "Tags", "text": shown + more, "wrapText": True}})
    if s["silenced"]:
        extra.append({"textParagraph": {"text": "🔕 <i>Silenced</i>"}})
    if s["link"]:
        extra.append({"buttonList": {"buttons": [{"text": "Open alarm", "onClick": {"openLink": {"url": s["link"]}}}]}})
    if extra:
        sections.append({"widgets": extra})
    if not sections:
        sections.append({"widgets": [{"textParagraph": {"text": esc(s["headline"])}}]})

    message: dict[str, Any] = {
        "cardsV2": [{
            "cardId": "alarm",
            "card": {"header": {"title": f"{s['emoji']} {s['headline']}", "subtitle": s["rule"]}, "sections": sections},
        }],
    }
    if s["alarm_id"]:
        message["thread"] = {"threadKey": s["alarm_id"]}
    return message


TRANSFORMS = {"slack": to_slack, "gchat": to_gchat}


# ---------- Config + delivery ----------

def validate_url(dest: str, url: str) -> Optional[str]:
    """Return an error message, or None if the URL is acceptable for this destination."""
    parts = urlsplit(url)
    hosts = DESTINATIONS[dest]["hosts"]
    if parts.scheme != "https" or (parts.hostname or "").lower() not in hosts or parts.port not in (None, 443):
        return f"Must be an https://{hosts[0]}/… webhook URL"
    if parts.username or parts.password or len(parts.path) < 2:
        return "That does not look like a complete webhook URL"
    return None


def mask_url(url: str) -> str:
    parts = urlsplit(url)
    return f"https://{parts.hostname}/…{url[-4:]}"


def _post(url: str, body: dict[str, Any]) -> tuple[Optional[int], str]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json; charset=UTF-8", "User-Agent": "webhook-receiver"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, resp.read(300).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return None, f"Connection failed: {e.reason}"
    except Exception as e:  # timeouts etc. — never let a delivery crash the worker
        return None, type(e).__name__


class Forwarder:
    def __init__(self):
        self._config: dict[str, dict[str, Any]] = {}
        self._deliveries: deque[dict[str, Any]] = deque(maxlen=100)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="forward")
        for dest, meta in DESTINATIONS.items():
            url = (os.environ.get(meta["env"]) or "").strip()
            ok = bool(url) and validate_url(dest, url) is None
            self._config[dest] = {
                "url": url if ok else None, "enabled": ok, "on_raised": True, "on_cleared": True, "from_env": ok,
            }

    # ----- config -----

    def describe(self) -> dict[str, Any]:
        out = {}
        for dest, meta in DESTINATIONS.items():
            c = self._config[dest]
            out[dest] = {
                "label": meta["label"], "env": meta["env"], "placeholder": meta["placeholder"],
                "configured": bool(c["url"]), "url_masked": mask_url(c["url"]) if c["url"] else None,
                "enabled": bool(c["url"]) and c["enabled"], "on_raised": c["on_raised"], "on_cleared": c["on_cleared"],
                "from_env": c["from_env"],
            }
        return out

    def update(self, dest: str, data: dict[str, Any]) -> Optional[str]:
        c = self._config[dest]
        url = str(data.get("url") or "").strip()
        if url:
            error = validate_url(dest, url)
            if error:
                return error
            c["url"], c["from_env"] = url, False
        for key in ("enabled", "on_raised", "on_cleared"):
            if key in data:
                c[key] = bool(data[key])
        if c["enabled"] and not c["url"]:
            c["enabled"] = False
            return "Add a webhook URL before turning forwarding on"
        return None

    def clear(self, dest: str) -> None:
        self._config[dest] = {"url": None, "enabled": False, "on_raised": True, "on_cleared": True, "from_env": False}

    def deliveries(self) -> list[dict[str, Any]]:
        return list(self._deliveries)

    # ----- delivery -----

    def _deliver(self, dest: str, url: str, payload: dict[str, Any], test: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        try:
            body = TRANSFORMS[dest](payload)
            if dest == "gchat" and "thread" in body:
                parts = urlsplit(url)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                query["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
                url = urlunsplit(parts._replace(query=urlencode(query)))
            status, detail = _post(url, body)
        except Exception as e:
            status, detail = None, f"Transform failed: {type(e).__name__}"
        ok = status is not None and 200 <= status < 300
        record = {
            "at": datetime.now(timezone.utc).isoformat(), "dest": dest, "label": DESTINATIONS[dest]["label"],
            "alarm_id": _clean(payload.get("alarmId")), "rule": _clean(payload.get("rule")),
            "state": _clean(payload.get("state")), "ok": ok, "status": status,
            "detail": "" if ok else " ".join(detail.split())[:200],
            "ms": int((time.monotonic() - started) * 1000), "test": test,
        }
        self._deliveries.appendleft(record)
        return record

    def dispatch(self, payload: dict[str, Any]) -> None:
        """Queue forwards for an incoming alarm. Returns immediately; never raises."""
        cleared = str(payload.get("state") or "").strip().upper() == "CLEARED"
        for dest, c in self._config.items():
            if c["url"] and c["enabled"] and (c["on_cleared"] if cleared else c["on_raised"]):
                self._pool.submit(self._deliver, dest, c["url"], payload)

    def send_test(self, dest: str) -> Optional[dict[str, Any]]:
        url = self._config[dest]["url"]
        return self._deliver(dest, url, SAMPLE_PAYLOAD, test=True) if url else None


forwarder = Forwarder()
