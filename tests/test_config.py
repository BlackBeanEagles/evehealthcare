"""DATABASE_URL parsing and env helpers."""
from __future__ import annotations

import pytest

from config.env import database_config, env_bool, env_list


@pytest.mark.parametrize(
    "url,expected",
    [
        ("sqlite:///dev.db", "dev.db"),
        # Four slashes is an absolute POSIX path. This regressed once: the
        # leading slash was stripped, and the container could not open its DB.
        ("sqlite:////tmp/dev.db", "/tmp/dev.db"),
        ("sqlite:///C:/data/dev.db", "C:/data/dev.db"),
        ("sqlite:///:memory:", ":memory:"),
    ],
)
def test_sqlite_urls(url, expected):
    config = database_config(url)

    assert config["ENGINE"] == "django.db.backends.sqlite3"
    assert config["NAME"] == expected


def test_postgres_url_including_escaped_credentials():
    config = database_config("postgresql://eve:p%40ss@db.internal:6543/eve_prod")

    assert config["ENGINE"] == "django.db.backends.postgresql"
    assert (config["USER"], config["PASSWORD"]) == ("eve", "p@ss")
    assert (config["HOST"], config["PORT"], config["NAME"]) == ("db.internal", "6543", "eve_prod")


def test_postgres_defaults_the_port():
    assert database_config("postgres://u:p@localhost/eve")["PORT"] == "5432"


def test_rejects_an_unsupported_scheme():
    with pytest.raises(ValueError):
        database_config("mysql://u:p@localhost/eve")


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("off", False), ("", True)])
def test_env_bool(monkeypatch, raw, expected):
    monkeypatch.setenv("SOME_FLAG", raw)

    assert env_bool("SOME_FLAG", default=True) is expected


def test_env_bool_rejects_nonsense(monkeypatch):
    monkeypatch.setenv("SOME_FLAG", "maybe")

    with pytest.raises(ValueError):
        env_bool("SOME_FLAG")


def test_env_list_trims_and_drops_blanks(monkeypatch):
    monkeypatch.setenv("HOSTS", " a.example.com, ,b.example.com ")

    assert env_list("HOSTS") == ["a.example.com", "b.example.com"]
