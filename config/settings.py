"""Settings for the EVE Healthcare diagnostics backend.

A single settings module driven entirely by environment variables. Defaults are
chosen so that the project runs out of the box for a reviewer; every value that
matters in production is overridable.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from config.env import database_config, env, env_bool, env_float, env_int, env_list

BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- core
SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-dev-key-do-not-use-in-production")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["*"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])
ENVIRONMENT = env("ENVIRONMENT", "local")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    # local
    "apps.common",
    "apps.accounts",
    "apps.catalog",
    "apps.bookings",
    "apps.payments",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.common.middleware.RequestLogMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ----------------------------------------------------------------------- database
DATABASES = {
    "default": database_config(
        env("DATABASE_URL", "postgresql://eve:eve@localhost:5432/eve_diagnostics")
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ------------------------------------------------------------------- i18n / static
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# ---------------------------------------------------------------------------- cache
REDIS_URL = env("REDIS_URL")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "eve",
        }
    }
else:
    # Keeps the project runnable (and the test suite fast) without Redis.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "eve-local",
        }
    }

CATALOG_CACHE_TTL_SECONDS = env_int("CATALOG_CACHE_TTL_SECONDS", 300)

# ----------------------------------------------------------------------------- DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.DefaultPagination",
    "PAGE_SIZE": env_int("API_PAGE_SIZE", 20),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", "60/min"),
        "user": env("THROTTLE_USER", "240/min"),
        "auth": env("THROTTLE_AUTH", "10/min"),
        "payments": env("THROTTLE_PAYMENTS", "20/min"),
        "webhook": env("THROTTLE_WEBHOOK", "600/min"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_MINUTES", 30)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("JWT_REFRESH_DAYS", 7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "SIGNING_KEY": env("JWT_SIGNING_KEY", SECRET_KEY),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "EVE Healthcare - Diagnostics Booking API",
    "DESCRIPTION": (
        "Book diagnostic tests at partner centres and pay for them through a "
        "simulated payment provider with an idempotent webhook."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "SORT_OPERATIONS": False,
    "PREPROCESSING_HOOKS": ["apps.common.schema.exclude_alias_paths"],
    "ENUM_NAME_OVERRIDES": {
        "BookingStatusEnum": "apps.bookings.models.BookingStatus.choices",
        "PaymentStatusEnum": "apps.payments.models.PaymentStatus.choices",
        "PaymentMethodEnum": "apps.payments.models.PaymentMethod.choices",
        "TestCategoryEnum": "apps.catalog.models.TestCategory.choices",
        "TransitionSourceEnum": "apps.bookings.models.TransitionSource.choices",
    },
    "TAGS": [
        {"name": "auth", "description": "Signup, login, refresh, logout."},
        {"name": "catalogue", "description": "Diagnostic centres, tests and prices."},
        {"name": "bookings", "description": "Book and cancel diagnostic tests."},
        {"name": "payments", "description": "Simulated payments and the idempotent webhook."},
        {"name": "ops", "description": "Health check."},
    ],
}

# -------------------------------------------------------------------------- celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL or "memory://")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", REDIS_URL or "cache+memory://")
# With no real broker configured we run tasks inline, so the service still
# behaves correctly in a single-container or test setup.
CELERY_TASK_ALWAYS_EAGER = env_bool(
    "CELERY_TASK_ALWAYS_EAGER", not bool(env("CELERY_BROKER_URL") or REDIS_URL)
)
CELERY_TASK_EAGER_PROPAGATES = env_bool("CELERY_TASK_EAGER_PROPAGATES", False)
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = TIME_ZONE

# ------------------------------------------------------------------------ business
# Booking rules
BOOKING_MIN_LEAD_TIME_MINUTES = env_int("BOOKING_MIN_LEAD_TIME_MINUTES", 30)
BOOKING_MAX_ADVANCE_DAYS = env_int("BOOKING_MAX_ADVANCE_DAYS", 90)
BOOKING_CANCELLATION_CUTOFF_HOURS = env_int("BOOKING_CANCELLATION_CUTOFF_HOURS", 2)

# Simulated payment provider
PAYMENT_CURRENCY = env("PAYMENT_CURRENCY", "INR")
PAYMENT_DEFAULT_OUTCOME = env("PAYMENT_DEFAULT_OUTCOME", "random")
PAYMENT_SUCCESS_RATE = env_float("PAYMENT_SUCCESS_RATE", 0.8)
PAYMENT_WEBHOOK_SECRET = env("PAYMENT_WEBHOOK_SECRET", "dev-webhook-secret")
PAYMENT_WEBHOOK_TOLERANCE_SECONDS = env_int("PAYMENT_WEBHOOK_TOLERANCE_SECONDS", 300)
PAYMENT_WEBHOOK_REQUIRE_SIGNATURE = env_bool("PAYMENT_WEBHOOK_REQUIRE_SIGNATURE", True)
PAYMENT_WEBHOOK_MAX_RETRIES = env_int("PAYMENT_WEBHOOK_MAX_RETRIES", 5)
PAYMENT_WEBHOOK_RETRY_BACKOFF_SECONDS = env_int("PAYMENT_WEBHOOK_RETRY_BACKOFF_SECONDS", 10)

# -------------------------------------------------------------------------- logging
LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOG_FORMAT = env("LOG_FORMAT", "console" if DEBUG else "json")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "apps.common.logging.RequestIDFilter"},
    },
    "formatters": {
        "json": {
            "()": "apps.common.logging.JSONFormatter",
        },
        "console": {
            "format": "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] %(message)s",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": LOG_FORMAT,
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["stdout"], "level": "WARNING"},
    "loggers": {
        "apps": {"handlers": ["stdout"], "level": LOG_LEVEL, "propagate": False},
        "django.request": {"handlers": ["stdout"], "level": "ERROR", "propagate": False},
        "celery": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}

# ------------------------------------------------------------------------- security
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
