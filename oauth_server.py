import base64
import os
from typing import Optional, Tuple

from flask import Flask, jsonify, request
from flask_cors import CORS

from jwt_shared import issue_access_token

app = Flask(__name__)
CORS(app)


def _configured_clients() -> dict[str, dict[str, object]]:
    """
    Two pre-configured clients.
    You can override secrets via env vars:
      - OAUTH_FULL_CLIENT_SECRET
      - OAUTH_LIMITED_CLIENT_SECRET
    """
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
    # OAuth2-style error response
    return jsonify({"error": error, "error_description": description}), status


def _parse_basic_auth(header: str) -> Tuple[Optional[str], Optional[str]]:
    if not header:
        return None, None
    if not header.lower().startswith("basic "):
        return None, None
    try:
        b64 = header.split(" ", 1)[1].strip()
        raw = base64.b64decode(b64).decode("utf-8")
        client_id, client_secret = raw.split(":", 1)
        return client_id, client_secret
    except Exception:
        return None, None


def _read_credentials() -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Returns (client_id, client_secret, grant_type, scope_request).
    Accepts:
      - HTTP Basic auth
      - application/x-www-form-urlencoded
      - JSON body
    """
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
        # form or anything else
        cid = cid or request.form.get("client_id")
        csec = csec or request.form.get("client_secret")
        grant_type = request.form.get("grant_type")
        scope_req = request.form.get("scope")

    return cid, csec, grant_type, scope_req


def _normalize_scope(scope_value: Optional[str]) -> set[str]:
    if not scope_value:
        return set()
    return {p for p in str(scope_value).split() if p}


@app.get("/health")
def health():
    return jsonify({"ok": True}), 200


@app.get("/.well-known/openid-configuration")
def discovery():
    issuer = os.environ.get("OAUTH_ISSUER", "http://localhost:5001")
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
def token():
    client_id, client_secret, grant_type, scope_req = _read_credentials()

    if (grant_type or "client_credentials") != "client_credentials":
        return _oauth_error(400, "unsupported_grant_type", "Only client_credentials is supported")

    if not client_id or not client_secret:
        return _oauth_error(400, "invalid_request", "Missing client credentials")

    clients = _configured_clients()
    cfg = clients.get(str(client_id))
    if not cfg or str(cfg["secret"]) != str(client_secret):
        return _oauth_error(401, "invalid_client", "Invalid client credentials")

    allowed_scopes = set(cfg["scopes"])  # type: ignore[arg-type]
    requested_scopes = _normalize_scope(scope_req)
    granted = allowed_scopes if not requested_scopes else (allowed_scopes & requested_scopes)
    scope_str = " ".join(sorted(granted))

    access_token = issue_access_token(client_id=str(client_id), scope=scope_str)
    return (
        jsonify(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": int(os.environ.get("TOKEN_EXPIRES_IN", "3600") or "3600"),
                "scope": scope_str,
            }
        ),
        200,
    )


if __name__ == "__main__":
    port = int(os.environ.get("OAUTH_PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)

