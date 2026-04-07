import os
from functools import wraps
from typing import Any, Callable, Optional

from flask import Flask, jsonify, request
from flask_cors import CORS

from jwt_shared import TokenError, decode_and_validate, require_scope, token_scopes

app = Flask(__name__)
CORS(app)


def _error(status: int, error: str, message: str):
    return jsonify({"error": error, "message": message}), status


def _extract_bearer_token() -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def require_auth(required_scope: Optional[str] = None):
    def decorator(fn: Callable[..., Any]):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = _extract_bearer_token()
            if not token:
                return _error(401, "missing_token", "Authorization Bearer token is required")
            try:
                claims = decode_and_validate(token)
            except TokenError as e:
                return _error(401, e.error, e.message)
            try:
                require_scope(claims, required_scope)
            except TokenError as e:
                # valid token but missing scope
                if e.error == "insufficient_scope":
                    return _error(403, "insufficient_scope", e.message)
                return _error(403, e.error, e.message)

            request.jwt_claims = claims  # type: ignore[attr-defined]
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@app.get("/health")
def health():
    return jsonify({"ok": True}), 200


@app.get("/api/status")
@require_auth(required_scope=None)  # any valid token
def api_status():
    claims = getattr(request, "jwt_claims", {})  # type: ignore[attr-defined]
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
@require_auth(required_scope="read")
def api_data_list():
    return (
        jsonify(
            {
                "items": [
                    {"id": "a1", "name": "alpha", "value": 1},
                    {"id": "b2", "name": "bravo", "value": 2},
                    {"id": "c3", "name": "charlie", "value": 3},
                ]
            }
        ),
        200,
    )


@app.post("/api/data")
@require_auth(required_scope="write")
def api_data_create():
    if not request.is_json:
        return _error(400, "invalid_request", "Content-Type must be application/json")
    payload = request.get_json(silent=True)
    if payload is None:
        return _error(400, "invalid_request", "Invalid JSON body")
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
@require_auth(required_scope="admin")
def api_admin_config():
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


if __name__ == "__main__":
    port = int(os.environ.get("API_PORT", "5002"))
    app.run(host="0.0.0.0", port=port, debug=True)

