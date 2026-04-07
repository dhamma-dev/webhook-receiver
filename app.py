import os

import base64

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, Response
from flask_cors import CORS

from auth import verify_user, login_required
from storage import store, payload_inspect_store
from jwt_shared import issue_access_token, decode_and_validate, TokenError, require_scope, token_scopes

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
CORS(app)


# ---------- Webhook (no auth so monitoring can POST) ----------

@app.route("/webhook/alarms", methods=["POST"])
def webhook_alarms():
    """Receive alarm events (RAISED/CLEARED). Store and return 200."""
    if not request.is_json:
        return jsonify({"ok": False, "error": "Content-Type must be application/json"}), 400
    payload = request.get_json()
    if not payload:
        return jsonify({"ok": False, "error": "Empty body"}), 400
    alarm_id = payload.get("alarmId")
    state = payload.get("state")
    if not alarm_id or not state:
        return jsonify({"ok": False, "error": "Missing alarmId or state"}), 400
    record = store.add_event(payload)
    return jsonify({"ok": True, "id": record["id"]}), 200


@app.route("/webhook/alarms/health", methods=["GET"])
def webhook_health():
    """Liveness for Render/monitoring."""
    return jsonify({"ok": True}), 200


# ---------- OAuth token server (client_credentials) ----------

def _configured_oauth_clients() -> dict[str, dict[str, object]]:
    return {
        "full-client": {
            "secret": os.environ.get("OAUTH_FULL_CLIENT_SECRET", "full-secret"),
            "scopes": {"read", "write", "admin"},
        },
        "limited-client": {
            "secret": os.environ.get("OAUTH_LIMITED_CLIENT_SECRET", "limited-secret"),
            "scopes": {"read"},
        },
    }


def _oauth_error(status: int, error: str, description: str):
    return jsonify({"error": error, "error_description": description}), status


def _parse_basic_auth(header: str):
    if not header or not header.lower().startswith("basic "):
        return None, None
    try:
        b64 = header.split(" ", 1)[1].strip()
        raw = base64.b64decode(b64).decode("utf-8")
        client_id, client_secret = raw.split(":", 1)
        return client_id, client_secret
    except Exception:
        return None, None


def _read_oauth_credentials():
    cid, csec = _parse_basic_auth(request.headers.get("Authorization", ""))
    grant_type = None
    scope_req = None

    if request.is_json:
        body = request.get_json(silent=True) or {}
        cid = cid or body.get("client_id")
        csec = csec or body.get("client_secret")
        grant_type = body.get("grant_type")
        scope_req = body.get("scope")
    else:
        cid = cid or request.form.get("client_id")
        csec = csec or request.form.get("client_secret")
        grant_type = request.form.get("grant_type")
        scope_req = request.form.get("scope")

    return cid, csec, grant_type, scope_req


def _normalize_scope(scope_value):
    if not scope_value:
        return set()
    return {p for p in str(scope_value).split() if p}


@app.get("/.well-known/openid-configuration")
def oidc_discovery():
    issuer = os.environ.get("OAUTH_ISSUER") or request.host_url.rstrip("/")
    token_url = issuer.rstrip("/") + "/token"
    return jsonify(
        {
            "issuer": issuer,
            "token_endpoint": token_url,
            "grant_types_supported": ["client_credentials"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
            ],
        }
    ), 200


@app.post("/token")
def oauth_token():
    client_id, client_secret, grant_type, scope_req = _read_oauth_credentials()

    if (grant_type or "client_credentials") != "client_credentials":
        return _oauth_error(400, "unsupported_grant_type", "Only client_credentials is supported")

    if not client_id or not client_secret:
        return _oauth_error(400, "invalid_request", "Missing client credentials")

    clients = _configured_oauth_clients()
    cfg = clients.get(str(client_id))
    if not cfg or str(cfg["secret"]) != str(client_secret):
        return _oauth_error(401, "invalid_client", "Invalid client credentials")

    allowed_scopes = set(cfg["scopes"])  # type: ignore[arg-type]
    requested_scopes = _normalize_scope(scope_req)
    granted = allowed_scopes if not requested_scopes else (allowed_scopes & requested_scopes)
    scope_str = " ".join(sorted(granted))

    # Ensure the token's issuer matches discovery unless overridden.
    os.environ.setdefault("OAUTH_ISSUER", os.environ.get("OAUTH_ISSUER") or request.host_url.rstrip("/"))

    access_token = issue_access_token(client_id=str(client_id), scope=scope_str)
    try:
        expires_in = int(os.environ.get("TOKEN_EXPIRES_IN", "3600") or "3600")
    except ValueError:
        expires_in = 3600

    return (
        jsonify(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": expires_in,
                "scope": scope_str,
            }
        ),
        200,
    )


# ---------- Protected API (Bearer JWT + scopes) ----------

def _api_error(status: int, error: str, message: str):
    return jsonify({"error": error, "message": message}), status


def _extract_bearer_token():
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _require_bearer(required_scope=None):
    token = _extract_bearer_token()
    if not token:
        return None, _api_error(401, "missing_token", "Authorization Bearer token is required")
    try:
        claims = decode_and_validate(token)
    except TokenError as e:
        return None, _api_error(401, e.error, e.message)
    try:
        require_scope(claims, required_scope)
    except TokenError as e:
        if e.error == "insufficient_scope":
            return None, _api_error(403, "insufficient_scope", e.message)
        return None, _api_error(403, e.error, e.message)
    return claims, None


@app.get("/health")
def poc_health():
    # Simple health for the POC endpoints
    return jsonify({"ok": True}), 200


@app.get("/api/status")
def poc_api_status():
    claims, err = _require_bearer(required_scope=None)
    if err:
        return err
    return (
        jsonify(
            {
                "ok": True,
                "aud": claims.get("aud"),
                "iss": claims.get("iss"),
                "sub": claims.get("sub"),
                "scope": sorted(list(token_scopes(claims))),
            }
        ),
        200,
    )


@app.get("/api/data")
def poc_api_data_list():
    claims, err = _require_bearer(required_scope="read")
    if err:
        return err
    return (
        jsonify(
            {
                "items": [
                    {"id": "a1", "name": "alpha", "value": 1},
                    {"id": "b2", "name": "bravo", "value": 2},
                    {"id": "c3", "name": "charlie", "value": 3},
                ],
                "caller": {"sub": claims.get("sub")},
            }
        ),
        200,
    )


@app.post("/api/data")
def poc_api_data_create():
    _claims, err = _require_bearer(required_scope="write")
    if err:
        return err
    if not request.is_json:
        return _api_error(400, "invalid_request", "Content-Type must be application/json")
    payload = request.get_json(silent=True)
    if payload is None:
        return _api_error(400, "invalid_request", "Invalid JSON body")
    return (
        jsonify(
            {
                "created": True,
                "item": {
                    "id": "new-1",
                    "payload": payload,
                },
            }
        ),
        201,
    )


@app.get("/api/admin/config")
def poc_api_admin_config():
    _claims, err = _require_bearer(required_scope="admin")
    if err:
        return err
    return (
        jsonify(
            {
                "service": "protected-api",
                "mode": os.environ.get("API_MODE", "poc"),
                "audience": os.environ.get("API_AUDIENCE", "protected-api"),
            }
        ),
        200,
    )


# ---------- Inspect: arbitrary POST payloads (no auth on POST) ----------

@app.route("/webhook/inspect", methods=["POST"])
def webhook_inspect():
    """Accept any POST body; store for later inspection. Returns 200 with id."""
    content_type = request.content_type or ""
    raw = request.get_data(as_text=True)
    try:
        parsed = request.get_json(silent=True)
    except Exception:
        parsed = None
    # Store a subset of headers (no cookies/auth)
    headers = {}
    for k, v in request.headers:
        if k.lower() in ("content-type", "content-length", "user-agent", "x-request-id") or k.lower().startswith("x-"):
            headers[k] = v
    record = payload_inspect_store.add(
        content_type=content_type,
        headers=headers,
        raw_body=raw,
        parsed_body=parsed,
    )
    return jsonify({"ok": True, "id": record["id"]}), 200


# ---------- Auth ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", next=request.args.get("next"))
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.form.get("next") or url_for("index")
    if not username or not verify_user(username, password):
        return render_template("login.html", error="Invalid username or password", next=next_url), 401
    session["user"] = username
    return redirect(next_url)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


# ---------- Dashboard (login required) ----------

@app.route("/")
@login_required
def index():
    """Dashboard: list alarm events with optional filters."""
    alarm_id = request.args.get("alarm_id", "").strip() or None
    state = request.args.get("state", "").strip() or None
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        limit = 100
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0
    events = store.get_events(alarm_id=alarm_id, state=state, limit=limit, offset=offset)
    return render_template(
        "dashboard.html",
        events=events,
        alarm_id_filter=alarm_id or "",
        state_filter=state or "",
        limit=limit,
        offset=offset,
        total=store.count(),
    )


def _api_events_allowed():
    """Allow access via session or X-API-Key header (for partner service)."""
    if session.get("user"):
        return True
    key = os.environ.get("RECEIVER_API_KEY")
    if key and request.headers.get("X-API-Key") == key:
        return True
    return False


@app.route("/api/events")
def api_events():
    """JSON list of events. Auth: session or X-API-Key. Supports org_id, connector_id, since, until for partner."""
    if not _api_events_allowed():
        if request.headers.get("X-API-Key") is not None:
            return jsonify({"error": "Invalid or missing API key"}), 401
        return redirect(url_for("login", next=request.url))
    alarm_id = request.args.get("alarm_id", "").strip() or None
    state = request.args.get("state", "").strip() or None
    org_id = request.args.get("org_id", "").strip() or None
    connector_id = request.args.get("connector_id", "").strip() or None
    since = request.args.get("since", "").strip() or None
    until = request.args.get("until", "").strip() or None
    try:
        limit = min(int(request.args.get("limit", 100)), 2000)
    except ValueError:
        limit = 100
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0
    events = store.get_events(
        alarm_id=alarm_id,
        state=state,
        org_id=org_id,
        connector_id=connector_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return jsonify({
        "events": events,
        "total": store.count(),
        "limit": limit,
        "offset": offset,
    })


@app.route("/event/<event_id>")
@login_required
def event_detail(event_id):
    """Single event: full JSON payload."""
    event = store.get_event_by_id(event_id)
    if not event:
        return "Event not found", 404
    return render_template("event_detail.html", event=event)


@app.route("/alarm/<alarm_id>")
@login_required
def alarm_pair(alarm_id):
    """All events for one alarm_id (RAISED + CLEARED pairing)."""
    events = store.get_events_by_alarm_id(alarm_id)
    if not events:
        return "No events found for this alarm", 404
    return render_template("alarm_pair.html", alarm_id=alarm_id, events=events)


# ---------- Inspect dashboard (login required) ----------

@app.route("/inspect")
@login_required
def inspect_index():
    """List recent arbitrary payloads received at POST /webhook/inspect."""
    org_id = (request.args.get("org_id") or "").strip() or None
    type_filter = (request.args.get("type") or "").strip() or None
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0
    total_matching = payload_inspect_store.count(org_id=org_id, type_val=type_filter)
    items = payload_inspect_store.get_all(
        limit=limit,
        offset=offset,
        org_id=org_id,
        type_val=type_filter,
    )
    return render_template(
        "inspect.html",
        items=items,
        total=total_matching,
        total_unfiltered=payload_inspect_store.count(),
        limit=limit,
        offset=offset,
        org_id_filter=org_id or "",
        type_filter=type_filter or "",
    )


@app.route("/inspect/export")
@login_required
def inspect_export():
    """Export filtered inspect payloads as JSON for analysis."""
    org_id = (request.args.get("org_id") or "").strip() or None
    type_filter = (request.args.get("type") or "").strip() or None
    payloads = payload_inspect_store.get_all_for_export(
        org_id=org_id,
        type_val=type_filter,
    )
    body = jsonify({"payloads": payloads, "count": len(payloads)}).get_data(as_text=True)
    resp = Response(body, mimetype="application/json")
    resp.headers["Content-Disposition"] = "attachment; filename=inspect-export.json"
    return resp


@app.route("/inspect/<item_id>")
@login_required
def inspect_detail(item_id):
    """Single payload: headers and body as received."""
    item = payload_inspect_store.get_by_id(item_id)
    if not item:
        return "Payload not found", 404
    return render_template("inspect_detail.html", item=item)


if __name__ == "__main__":
    app.run(debug=True)
