"""
Single-user auth from environment variables (plain text).
Set DASHBOARD_USER and DASHBOARD_PASSWORD to enable login.
"""
import os
from functools import wraps

from flask import session, redirect, request, url_for

DASHBOARD_USER_ENV = "DASHBOARD_USER"
DASHBOARD_PASSWORD_ENV = "DASHBOARD_PASSWORD"


def verify_user(username: str, password: str) -> bool:
    expected_user = os.environ.get(DASHBOARD_USER_ENV)
    expected_password = os.environ.get(DASHBOARD_PASSWORD_ENV)
    if not expected_user or not expected_password:
        return False
    return username == expected_user and password == expected_password


def login_required(f):
    """Decorator: redirect to login if not in session. Webhook and health routes stay public."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if session.get("user") is None:
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return wrapped
