"""Django settings for ICEA Platform MVP.

Goal: a deployable baseline (local + Docker) that exposes a minimal REST API for
training an ICEA model and computing ICEA/ICEA+ contributions using SHAP.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "unsafe-secret-for-dev")
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() in {"1", "true", "yes"}

# Hardening: do NOT default to wildcard hosts in institutional environments.
# For local/dev, allow localhost/127.0.0.1 by default.
_default_hosts = "localhost,127.0.0.1" if DEBUG else ""
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", _default_hosts).split(",") if h.strip()]

# --- Apps
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "icea_core",
    "icea_pipeline.apps.IceaPipelineConfig",
]

# Optional row-level history (django-simple-history) — enterprise-only.
ICEA_ENABLE_SIMPLE_HISTORY = os.environ.get("ICEA_ENABLE_SIMPLE_HISTORY", "false").lower() in {"1", "true", "yes"}
if ICEA_ENABLE_SIMPLE_HISTORY:
    try:
        import simple_history  # type: ignore  # noqa: F401

        INSTALLED_APPS = ["simple_history", *INSTALLED_APPS]
    except Exception:
        ICEA_ENABLE_SIMPLE_HISTORY = False

# --- Enterprise feature flags (graceful degradation)
ICEA_ENABLE_CHANNELS = os.environ.get("ICEA_ENABLE_CHANNELS", "false").lower() in {"1", "true", "yes"}
if ICEA_ENABLE_CHANNELS:
    # Only enable Channels if the optional dependency is installed.
    try:
        import channels  # type: ignore  # noqa: F401

        # Prepend so it loads early.
        INSTALLED_APPS = ["channels", *INSTALLED_APPS]

        # MVP default: in-memory channel layer (swap for Redis in production).
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    except Exception:
        ICEA_ENABLE_CHANNELS = False

# --- Zero-Trust perimeter flags (v0.7.2+)
ICEA_ENABLE_REQUEST_LIMITS = os.environ.get("ICEA_ENABLE_REQUEST_LIMITS", "false").lower() in {"1", "true", "yes"}




MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "config.middleware.OptionalAPIKeyMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Captures request actor (user/header) for governance + lineage logs.
    "config.request_context.RequestActorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Insert request size limits early in the chain (before DRF parsing) when enabled.
if ICEA_ENABLE_REQUEST_LIMITS:
    # After SecurityMiddleware for correct security headers.
    try:
        MIDDLEWARE.insert(1, "icea_core.middleware.size_limit.RequestSizeLimitMiddleware")
    except Exception:
        pass

if ICEA_ENABLE_SIMPLE_HISTORY:
    # Request middleware for django-simple-history (best effort)
    MIDDLEWARE.append("simple_history.middleware.HistoryRequestMiddleware")

ROOT_URLCONF = "config.urls"

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
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- DB
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)}
else:
    # Docker defaults to Postgres service "postgres" in production. Local dev can override via POSTGRES_HOST (e.g., "db").
    if os.environ.get("POSTGRES_HOST") or os.environ.get("POSTGRES_DB"):
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": os.environ.get("POSTGRES_DB", "icea_db"),
                "USER": os.environ.get("POSTGRES_USER", "icea_user"),
                "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "icea_pass"),
                "HOST": os.environ.get("POSTGRES_HOST", "postgres"),
                "PORT": int(os.environ.get("POSTGRES_PORT", "5432")),
            }
        }
    else:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }
        }

# --- Cache (required for anti-replay + distributed throttling)
# ENS Alto deployments SHOULD configure Redis via REDIS_URL.
# Graceful degradation: if REDIS_URL is not provided, fall back to LocMemCache
# (sufficient for single-process dev only).
REDIS_URL = (os.environ.get("REDIS_URL") or "").strip()
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                # Health/observability-friendly defaults
                "SOCKET_CONNECT_TIMEOUT": float(os.environ.get("REDIS_CONNECT_TIMEOUT", "3")),
                "SOCKET_TIMEOUT": float(os.environ.get("REDIS_SOCKET_TIMEOUT", "3")),
            },
            "KEY_PREFIX": os.environ.get("ICEA_CACHE_KEY_PREFIX", "icea"),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "icea-locmem",
        }
    }

# --- Auth
AUTH_PASSWORD_VALIDATORS = [] if DEBUG else [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Security hardening (ENS Alto / EU AI Act)
# Secure mode is OFF for local/dev. Sensitive ICEA views still fail closed unless
# ICEA_DEV_ALLOW_INSECURE=true is explicitly set for local demos/tests.
ICEA_SECURE_MODE = os.environ.get("ICEA_SECURE_MODE", "false").lower() in {"1", "true", "yes"}
ICEA_DEV_ALLOW_INSECURE = os.environ.get("ICEA_DEV_ALLOW_INSECURE", "false").lower() in {"1", "true", "yes"}
ICEA_AUTH_REQUIRED = os.environ.get("ICEA_AUTH_REQUIRED", "true" if not ICEA_DEV_ALLOW_INSECURE else "false").lower() in {"1", "true", "yes"}
ICEA_RBAC_ENFORCE = os.environ.get("ICEA_RBAC_ENFORCE", "true" if not ICEA_DEV_ALLOW_INSECURE else "false").lower() in {"1", "true", "yes"}

# ENS Alto: never allow the development SECRET_KEY in secure deployments.
# This prevents accidental production deployments with a predictable key.
if ICEA_SECURE_MODE and SECRET_KEY == "unsafe-secret-for-dev":
    raise ImproperlyConfigured(
        "ICEA_SECURE_MODE=true but SECRET_KEY is the development default. "
        "Set SECRET_KEY to a strong, unique value."
    )

if ICEA_SECURE_MODE:
    if ICEA_DEV_ALLOW_INSECURE:
        raise ImproperlyConfigured("ICEA_SECURE_MODE=true cannot be combined with ICEA_DEV_ALLOW_INSECURE=true.")
    if not ICEA_AUTH_REQUIRED:
        raise ImproperlyConfigured("ICEA_SECURE_MODE=true requires ICEA_AUTH_REQUIRED=true.")
    if not ICEA_RBAC_ENFORCE:
        raise ImproperlyConfigured("ICEA_SECURE_MODE=true requires ICEA_RBAC_ENFORCE=true.")
    if not (os.environ.get("JWT_SIGNING_KEY") or os.environ.get("JWT_VERIFYING_KEY") or os.environ.get("OIDC_JWKS_URL")):
        raise ImproperlyConfigured(
            "ICEA_SECURE_MODE=true requires JWT_SIGNING_KEY, JWT_VERIFYING_KEY, or OIDC_JWKS_URL. "
            "Do not rely on the Django SECRET_KEY for clinical/high-risk API tokens."
        )

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = os.environ.get("X_FRAME_OPTIONS", "DENY")
REFERRER_POLICY = os.environ.get("REFERRER_POLICY", "strict-origin-when-cross-origin")

if ICEA_SECURE_MODE and not DEBUG:
    # If behind a TLS-terminating proxy/load balancer.
    if os.environ.get("SECURE_PROXY_SSL_HEADER", "true").lower() in {"1", "true", "yes"}:
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Host/proxy headers (required for correct absolute URL generation + reverse-proxy deployments).
    USE_X_FORWARDED_HOST = os.environ.get("USE_X_FORWARDED_HOST", "true").lower() in {"1", "true", "yes"}
    USE_X_FORWARDED_PORT = os.environ.get("USE_X_FORWARDED_PORT", "true").lower() in {"1", "true", "yes"}
    SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "true").lower() in {"1", "true", "yes"}
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    CSRF_COOKIE_SAMESITE = os.environ.get("CSRF_COOKIE_SAMESITE", "Lax")
    # HSTS
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get("SECURE_HSTS_INCLUDE_SUBDOMAINS", "true").lower() in {"1", "true", "yes"}
    SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "true").lower() in {"1", "true", "yes"}

# --- CORS (hardened defaults)
# In institutional deployments, set CORS_ALLOWED_ORIGINS explicitly.
CORS_ALLOW_ALL_ORIGINS = os.environ.get("CORS_ALLOW_ALL_ORIGINS", "false").lower() in {"1", "true", "yes"}
if not CORS_ALLOW_ALL_ORIGINS:
    CORS_ALLOWED_ORIGINS = [
        o.strip()
        for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
        if o.strip()
    ]
    # Local developer UX (only when DEBUG)
    if DEBUG and not CORS_ALLOWED_ORIGINS:
        CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173"]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

# --- DRF / OpenAPI
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # JWT authentication is always enabled (backwards compatible: endpoints can remain AllowAny).
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        # Keep SessionAuth for /admin/ and local debugging.
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Fail-closed by default; local demos must explicitly set ICEA_DEV_ALLOW_INSECURE=true.
    "DEFAULT_PERMISSION_CLASSES": ["icea_core.permissions.ICEABackwardCompatiblePermission"],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# --- DRF throttling (Zero-Trust perimeter)
# Scoped throttling is on by default. Operators may explicitly disable it only
# for isolated local development.
ICEA_ENABLE_THROTTLING = os.environ.get("ICEA_ENABLE_THROTTLING", "true").lower() in {"1", "true", "yes"}
ICEA_ENABLE_GLOBAL_THROTTLING = os.environ.get("ICEA_ENABLE_GLOBAL_THROTTLING", "false").lower() in {"1", "true", "yes"}
ICEA_ANON_RATE_LIMIT = os.environ.get("ICEA_ANON_RATE_LIMIT", "100/day")
ICEA_USER_RATE_LIMIT = os.environ.get("ICEA_USER_RATE_LIMIT", "1000/hour")

# Scoped throttling is safe to enable globally because it only applies to views
# that define `throttle_scope = "..."`.
# Define scope rates via env vars like:
#   ICEA_THROTTLE_SCOPE_INGEST="5000/hour"
#   ICEA_THROTTLE_SCOPE_FEDERATED="50/day"
ICEA_THROTTLE_SCOPE_PREFIX = "ICEA_THROTTLE_SCOPE_"
ICEA_RUNNING_TESTS = "test" in sys.argv


def _icea_scope_rate(scope: str, production_default: str) -> str:
    configured = os.environ.get(f"ICEA_THROTTLE_SCOPE_{scope.upper()}")
    if configured:
        return configured
    return "10000/hour" if ICEA_RUNNING_TESTS else production_default

if ICEA_ENABLE_THROTTLING:
    REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = ["icea_core.throttling.IceaScopedRateThrottle"]
    if ICEA_ENABLE_GLOBAL_THROTTLING:
        REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = [
            "icea_core.throttling.IceaAnonRateThrottle",
            "icea_core.throttling.IceaUserRateThrottle",
            *REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"],
        ]

    # Conservative defaults, overridable with ICEA_THROTTLE_SCOPE_<SCOPE>.
    rates = {
        "anon": ICEA_ANON_RATE_LIMIT,
        "user": ICEA_USER_RATE_LIMIT,
        "icea_read": _icea_scope_rate("icea_read", "600/hour"),
        "icea_compute": _icea_scope_rate("icea_compute", "180/hour"),
        "icea_train": _icea_scope_rate("icea_train", "20/hour"),
        "icea_export": _icea_scope_rate("icea_export", "60/hour"),
        "icea_writeback": _icea_scope_rate("icea_writeback", "30/hour"),
        "ingest": _icea_scope_rate("ingest", "500/hour"),
        "federated": _icea_scope_rate("federated", "20/day"),
    }

    # Optional scoped rates
    for k, v in os.environ.items():
        if not k.startswith(ICEA_THROTTLE_SCOPE_PREFIX):
            continue
        scope = k[len(ICEA_THROTTLE_SCOPE_PREFIX):].strip().lower()
        if not scope:
            continue
        rate = (v or "").strip()
        if not rate:
            continue
        rates[scope] = rate

    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = rates



# --- JWT / OIDC settings (SimpleJWT)
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.environ.get("JWT_ACCESS_MINUTES", "15"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.environ.get("JWT_REFRESH_DAYS", "7"))),
    "ROTATE_REFRESH_TOKENS": os.environ.get("JWT_ROTATE_REFRESH", "false").lower() in {"1", "true", "yes"},
    "BLACKLIST_AFTER_ROTATION": os.environ.get("JWT_BLACKLIST_AFTER_ROTATION", "false").lower() in {"1", "true", "yes"},
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": os.environ.get("JWT_ALGORITHM", "HS256"),
    # Recommended: provide a dedicated signing key in production.
    "SIGNING_KEY": os.environ.get("JWT_SIGNING_KEY", SECRET_KEY),
    "VERIFYING_KEY": os.environ.get("JWT_VERIFYING_KEY", ""),
    "AUDIENCE": os.environ.get("JWT_AUDIENCE") or None,
    "ISSUER": os.environ.get("JWT_ISSUER") or None,
    # OIDC JWKS endpoint (e.g., Auth0/Keycloak): enables dynamic key resolution.
    "JWK_URL": os.environ.get("OIDC_JWKS_URL") or None,
    "LEEWAY": int(os.environ.get("JWT_LEEWAY_SECONDS", "0")),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_CLAIM": os.environ.get("JWT_USER_ID_CLAIM", "user_id"),
}

# --- PHI retention policy (RawFHIRResource)
PHI_RETENTION_DAYS = int(os.environ.get("PHI_RETENTION_DAYS", "30"))
PHI_RETENTION_ACTION = os.environ.get("PHI_RETENTION_ACTION", "delete").strip().lower()  # delete|anonymize

# --- PHI encryption keys (Fernet-compatible; base64 urlsafe 32-byte keys)
# Provide one or more keys for rotation: "key1,key2,..." (key1 = newest).
PHI_ENCRYPTION_KEYS = [k.strip() for k in os.environ.get("PHI_ENCRYPTION_KEYS", "").split(",") if k.strip()]

SPECTACULAR_SETTINGS = {
    "TITLE": "ICEA Platform MVP",
    "DESCRIPTION": "Minimal API to train ICEA models and compute nursing contribution (ICEA) via SHAP.",
    "VERSION": os.environ.get("ICEA_VERSION", "0.7.3"),
}

# --- File storage for model artifacts
ICEA_MODEL_DIR = os.environ.get("ICEA_MODEL_DIR", str(BASE_DIR / "models"))

# --- Governance / Audit (v0.5)
AUDIT_LOG_SECRET = os.environ.get("AUDIT_LOG_SECRET", "")


# --- FHIR strict validation (enterprise, optional)
FHIR_STRICT_VALIDATION = os.environ.get("FHIR_STRICT_VALIDATION", "false").lower() in {"1", "true", "yes"}
FHIR_REQUIRED_PROFILES = [p.strip() for p in os.environ.get("FHIR_REQUIRED_PROFILES", "").split(",") if p.strip()]
FHIR_STRICT_FAIL_CLOSED = os.environ.get("FHIR_STRICT_FAIL_CLOSED", "false").lower() in {"1", "true", "yes"}
