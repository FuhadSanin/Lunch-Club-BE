"""Rate limits, so one person (or a leaked access token) can't hammer the API or burn
the quotas of the services it calls (Swiggy, the place search).

Every signed-in person gets a general limit. Some endpoints have their own, tighter
one, named by `throttle_scope` on the view; the rates are in settings
(`REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`).
"""

import logging

from django.core.cache import caches
from django.core.cache.backends.db import DatabaseCache
from django.db import DatabaseError, router, transaction
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

logger = logging.getLogger(__name__)


class FailOpenDatabaseCache(DatabaseCache):
    """A cache in the database, shared by every server instance, that never breaks a
    request: if the table is missing or the database hiccups, the limit is skipped.

    Serverless hosting has no memory shared between instances, so counting in memory
    would let each instance allow the full rate. Each call runs in its own savepoint, so
    a failure can't poison the request's transaction.
    """

    def _safely(self, action, default, name):
        db = router.db_for_write(self.cache_model_class)
        try:
            with transaction.atomic(using=db):
                return action()
        except DatabaseError:
            logger.warning("Rate-limit cache unavailable (%s); not limiting", name)
            return default

    def get(self, key, default=None, version=None):
        return self._safely(
            lambda: super(FailOpenDatabaseCache, self).get(key, default, version),
            default,
            "get",
        )

    def set(self, key, value, timeout=None, version=None):
        return self._safely(
            lambda: super(FailOpenDatabaseCache, self).set(
                key, value, timeout, version
            ),
            None,
            "set",
        )


class _ThrottleCache:
    """The `throttle` cache, looked up on use (settings can change in tests)."""

    def __getattr__(self, name):
        return getattr(caches["throttle"], name)


def _a_person(request) -> bool:
    """Only people are limited. Cloud Tasks calling the job endpoint is a system, not a
    person (it has no id to count against), and must never be refused."""
    return getattr(request.user, "pk", None) is not None


class UserThrottle(UserRateThrottle):
    """The general limit for each signed-in person."""

    cache = _ThrottleCache()  # type: ignore[assignment]

    def get_cache_key(self, request, view):
        return super().get_cache_key(request, view) if _a_person(request) else None


class PublicThrottle(ScopedRateThrottle):
    """For endpoints anyone can reach (assistants signing in): counted per address.
    Behind a proxy the first X-Forwarded-For entry is the caller (Vercel sets it)."""

    cache = _ThrottleCache()  # type: ignore[assignment]

    def get_ident(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")


class ScopedThrottle(ScopedRateThrottle):
    """The tighter limit of an endpoint that names a `throttle_scope`."""

    cache = _ThrottleCache()  # type: ignore[assignment]

    def get_cache_key(self, request, view):
        return super().get_cache_key(request, view) if _a_person(request) else None
