"""Tiny environment helpers.

Deliberately dependency-free: the project reads configuration straight from the
environment (12-factor) without pulling in django-environ.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import unquote, urlparse

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    raise ValueError(f"{name} must be a boolean-ish value, got {raw!r}")


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else float(raw)


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


def database_config(url: str) -> dict[str, Any]:
    """Parse a DATABASE_URL into Django's DATABASES['default'] shape.

    Supports ``postgres://``/``postgresql://`` and ``sqlite://`` URLs, which is
    all this project needs (Postgres in Docker/CI, SQLite for a zero-setup
    local test run).
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.replace("+psycopg2", "")

    if scheme == "sqlite":
        # Strip exactly one slash, per the usual database-URL convention:
        #   sqlite:///dev.db        -> dev.db        (relative)
        #   sqlite:////tmp/dev.db   -> /tmp/dev.db   (absolute, POSIX)
        #   sqlite:///C:/dev.db     -> C:/dev.db     (absolute, Windows)
        #   sqlite:///:memory:      -> :memory:
        path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": path or ":memory:"}

    if scheme not in {"postgres", "postgresql", "pgsql"}:
        raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")) or "postgres",
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
    }
