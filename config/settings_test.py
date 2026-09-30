"""Settings used by the test suite.

pytest-django configures Django during ``pytest_load_initial_conftests``,
before any ``conftest.py`` is imported, so test-only configuration has to
live in a settings module rather than in environment tweaks made from a
fixture.

The database defaults to in-memory SQLite so ``pytest`` works on a clean
checkout with nothing running. Point ``DATABASE_URL`` at Postgres - the
production target - to run the identical suite against it::

    DATABASE_URL=postgresql://eve:eve@localhost:5432/eve_diagnostics pytest
"""
from config.env import database_config, env
from config.settings import *  # noqa: F403

DATABASES = {"default": database_config(env("DATABASE_URL", "sqlite:///:memory:"))}

# Password hashing dominates the runtime of an auth-heavy suite otherwise.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "eve-test",
    }
}

# Webhooks are processed inline so assertions can run straight afterwards.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False

# Deterministic money: tests that want a decline ask for one explicitly.
PAYMENT_DEFAULT_OUTCOME = "success"
PAYMENT_WEBHOOK_SECRET = "test-webhook-secret"
PAYMENT_WEBHOOK_REQUIRE_SIGNATURE = True

# Throttling has its own test, which lowers the rate it cares about.
# Everywhere else a real limit would just make the suite flaky.
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100000/min",
        "user": "100000/min",
        "auth": "100000/min",
        "payments": "100000/min",
        "webhook": "100000/min",
    },
}

LOGGING["root"]["level"] = "CRITICAL"  # noqa: F405
for _logger in LOGGING["loggers"].values():  # noqa: F405
    _logger["level"] = "CRITICAL"

# The manifest storage needs `collectstatic` to have run; tests don't.
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
