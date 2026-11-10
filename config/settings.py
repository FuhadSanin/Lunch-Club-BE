"""Settings come from environment variables only; see .env.example for the full list."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes"}


SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env_bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# Sign-in is Firebase-only, so Django's auth, admin and session apps are not installed.
INSTALLED_APPS = [
    "django.contrib.postgres",  # ArrayField, for people's permissions
    "rest_framework",
    "apps.core",
    "apps.accounts",
    "apps.lunches",
    "apps.ordering",
    "apps.catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    # DRF views use bearer tokens, not cookies, so skip CSRF; anything else doesn't.
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "lunch"),
        "USER": env("DB_USER", "lunch"),
        "PASSWORD": env("DB_PASSWORD", ""),
        # On Cloud Run this is the Cloud SQL socket dir:
        # /cloudsql/<project>:<region>:<instance>
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", "5432"),
        "ATOMIC_REQUESTS": True,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Asia/Kolkata"
LANGUAGE_CODE = "en-in"

AUTHENTICATION_CLASSES = ["apps.accounts.auth.FirebaseAuthentication"]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": AUTHENTICATION_CLASSES,
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "UNAUTHENTICATED_USER": None,
}

# Local development only: requests without a token act as this person
# (apps.accounts.auth).
DEV_AUTH_EMAIL = env("DEV_AUTH_EMAIL", "")
if DEV_AUTH_EMAIL:
    if not DEBUG:
        raise RuntimeError(
            "DEV_AUTH_EMAIL is for local development; it needs DJANGO_DEBUG=true."
        )
    AUTHENTICATION_CLASSES.append("apps.accounts.auth.DevAuthentication")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# HTTPS only outside local development. Cloud Run's health checks arrive over plain
# HTTP inside Google's network, so /healthz is never redirected.
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", not DEBUG)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "0" if DEBUG else "2592000"))
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
# These cover every subdomain and are hard to undo, so they are the domain owner's call.
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS")
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD")
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

# --- Google Cloud / Firebase -------------------------------------------------
GCP_PROJECT = env("GCP_PROJECT", "")
GCP_REGION = env("GCP_REGION", "asia-south1")
# The Firebase project whose sign-in tokens the API accepts.
FIREBASE_PROJECT_ID = env("FIREBASE_PROJECT_ID", GCP_PROJECT)

# When off (local dev, tests) background jobs run inline and the live board is not
# written.
CLOUD_TASKS_ENABLED = env_bool("CLOUD_TASKS_ENABLED")
CLOUD_TASKS_QUEUE = env("CLOUD_TASKS_QUEUE", "lunch-jobs")
# Public URL of this service; Cloud Tasks posts to
# {TASKS_BASE_URL}/internal/tasks/<name>.
TASKS_BASE_URL = env("TASKS_BASE_URL", "")
# Service account that Cloud Tasks signs its OIDC tokens with.
TASKS_SERVICE_ACCOUNT = env("TASKS_SERVICE_ACCOUNT", "")
FIRESTORE_ENABLED = env_bool("FIRESTORE_ENABLED")
PUSH_ENABLED = env_bool("PUSH_ENABLED")

# Web app address, used for links in notifications.
APP_URL = env("APP_URL", "http://localhost:5173")

# --- Ordering ----------------------------------------------------------------
# Swiggy's multi-restaurant checkout takes at most this many restaurants per order.
MAX_RESTAURANTS_PER_ORDER = int(env("MAX_RESTAURANTS_PER_ORDER", "5"))
ORDER_PROVIDER = env("ORDER_PROVIDER", "apps.ordering.providers.manual.ManualProvider")
# A placement attempt that has not finished in this time is treated as crashed.
PLACEMENT_LEASE_SECONDS = int(env("PLACEMENT_LEASE_SECONDS", "600"))
DELIVERY_POLL_SECONDS = int(env("DELIVERY_POLL_SECONDS", "300"))

# --- Swiggy order bot (apps.ordering.providers.swiggy_web) ---------------------
# Attach to a Chrome the host is logged in to (started with --remote-debugging-port),
# e.g. http://127.0.0.1:9222. If empty, a Chromium with its own saved login is launched.
SWIGGY_BOT_CDP_URL = env("SWIGGY_BOT_CDP_URL", "")
SWIGGY_BOT_PROFILE_DIR = env("SWIGGY_BOT_PROFILE_DIR", "swiggy_browser_profile")
SWIGGY_BOT_HEADLESS = env_bool("SWIGGY_BOT_HEADLESS")
# Refuse to fill a cart when the browser isn't signed in to Swiggy: a guest cart can't
# be checked out from the host's account.
SWIGGY_BOT_REQUIRE_LOGIN = env_bool("SWIGGY_BOT_REQUIRE_LOGIN", True)
# Swiggy's cart holds one restaurant. When it still has another restaurant's items,
# press "Yes, start afresh" (for the order's first restaurant only) instead of stopping.
SWIGGY_BOT_REPLACE_CART = env_bool("SWIGGY_BOT_REPLACE_CART")
# A customisable dish asks for choices (e.g. which biryani). Choose the required ones:
# the option named in the order note, else the cheapest. What it chose is shown in the
# order's steps.
SWIGGY_BOT_AUTO_OPTIONS = env_bool("SWIGGY_BOT_AUTO_OPTIONS")

# --- Swiggy catalog ------------------------------------------------------------
# Restaurants, menus and dishes come from Swiggy's public web endpoints (apps.catalog).
SWIGGY_TIMEOUT_SECONDS = float(env("SWIGGY_TIMEOUT_SECONDS", "20"))
SWIGGY_CACHE_SECONDS = int(env("SWIGGY_CACHE_SECONDS", "600"))

# --- Email -------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "lunch@localhost")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)

# --- Logging and error alerts --------------------------------------------------
# One JSON object per line, which Cloud Logging parses into structured entries.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "apps.core.logging.JsonFormatter"}},
    "handlers": {"stdout": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["stdout"], "level": env("LOG_LEVEL", "INFO")},
}

SENTRY_DSN = env("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(dsn=SENTRY_DSN, send_default_pii=False, traces_sample_rate=0.1)
