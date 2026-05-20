from __future__ import annotations

TOKENS = {"learner-token": "learner"}


def login(username: str, password: str) -> str | None:
    if username == "learner" and password == "123456":
        return "learner-token"
    return None


def require_auth(token: str | None) -> bool:
    return bool(token and token in TOKENS)

