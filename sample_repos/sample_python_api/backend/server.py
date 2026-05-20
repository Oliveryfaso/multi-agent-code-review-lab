from __future__ import annotations

from backend.auth import login, require_auth


def login_handler(payload: dict) -> dict:
    token = login(payload.get("username", ""), payload.get("password", ""))
    if not token:
        return {"status": 401, "error": "invalid credentials"}
    return {"status": 200, "token": token}


def dashboard_handler(headers: dict) -> dict:
    token = headers.get("Authorization")
    if not require_auth(token):
        return {"status": 401, "error": "unauthorized"}
    return {"status": 200, "data": {"streak": 7}}


def profile_handler(headers: dict) -> dict:
    token = headers.get("Authorization")
    if not require_auth(token):
        return {"status": 401, "error": "unauthorized"}
    return {"status": 200, "data": {"name": "learner"}}

