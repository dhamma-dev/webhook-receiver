import os
import time
from typing import Any, Optional

import jwt


def _now() -> int:
    return int(time.time())


def get_jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "dev-jwt-secret-change-in-production")


def get_api_audience() -> str:
    return os.environ.get("API_AUDIENCE", "protected-api")


def get_oauth_issuer() -> str:
    return os.environ.get("OAUTH_ISSUER", "http://localhost:5001")


def get_token_expires_in() -> int:
    try:
        return int(os.environ.get("TOKEN_EXPIRES_IN", "3600"))
    except ValueError:
        return 3600


def issue_access_token(*, client_id: str, scope: str) -> str:
    iat = _now()
    exp = iat + get_token_expires_in()
    payload = {
        "iss": get_oauth_issuer(),
        "sub": client_id,
        "aud": get_api_audience(),
        "scope": scope,
        "iat": iat,
        "exp": exp,
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


class TokenError(Exception):
    def __init__(self, error: str, message: str):
        super().__init__(message)
        self.error = error
        self.message = message


def decode_and_validate(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            get_jwt_secret(),
            algorithms=["HS256"],
            audience=get_api_audience(),
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise TokenError("token_expired", "Token is expired") from e
    except jwt.InvalidAudienceError as e:
        raise TokenError("invalid_audience", "Token audience is invalid") from e
    except jwt.InvalidTokenError as e:
        raise TokenError("invalid_token", "Token is invalid") from e


def token_scopes(claims: dict[str, Any]) -> set[str]:
    s = claims.get("scope") or ""
    if isinstance(s, str):
        return {p for p in s.split() if p}
    if isinstance(s, list):
        return {str(p) for p in s if p}
    return set()


def require_scope(claims: dict[str, Any], required: Optional[str]) -> None:
    if not required:
        return
    scopes = token_scopes(claims)
    if required not in scopes:
        raise TokenError("insufficient_scope", f"Missing required scope: {required}")

