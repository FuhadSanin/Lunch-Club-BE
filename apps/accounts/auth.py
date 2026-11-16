import logging
from functools import cache

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from apps.accounts.services import (
    TOKEN_PREFIX,
    dev_user_claims,
    user_for_api_token,
    user_for_token,
)

logger = logging.getLogger(__name__)

# Firebase tokens are checked against Google's clock; allow ours to be a little off.
CLOCK_SKEW_SECONDS = 10


@cache
def _google_request():
    """An HTTP client that keeps Google's signing keys for as long as Google says."""
    import cachecontrol
    import requests
    from google.auth.transport.requests import Request

    return Request(session=cachecontrol.CacheControl(requests.Session()))


def verify_firebase_token(token: str) -> dict:
    """The claims of a Firebase ID token signed for this project, or ValueError.

    Only Google's public keys are needed, so this works without Google credentials.
    """
    from google.oauth2 import id_token

    project = settings.FIREBASE_PROJECT_ID
    claims = id_token.verify_firebase_token(
        token,
        _google_request(),
        audience=project,
        clock_skew_in_seconds=CLOCK_SKEW_SECONDS,
    )
    if claims.get("iss") != f"https://securetoken.google.com/{project}":
        raise ValueError("Token was issued for another Firebase project.")
    if not claims.get("sub"):
        raise ValueError("Token has no user.")
    return {**claims, "uid": claims["sub"]}


# What a personal access token may reach: the lunch itself, browsing, and notifications.
# Never managing tokens, the company's settings and people, or permissions: those need a
# real sign-in, so a leaked token can't mint more tokens or change who is in charge.
TOKEN_ALLOWED_PATHS = (
    "/api/me",
    "/api/lunches",
    "/api/local-menus",
    "/api/orders",
    "/api/restaurants",
    "/api/dishes",
    "/api/places",
    "/api/notifications",
)


class ApiTokenAuthentication(BaseAuthentication):
    """Authenticates `Authorization: Bearer lc_...`, a personal access token."""

    def authenticate(self, request):
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme != "Bearer" or not token.startswith(TOKEN_PREFIX):
            return None  # not ours: a Firebase sign-in token
        path = request.path.rstrip("/")
        if path.startswith("/api/me/") or not any(
            path == p or path.startswith(p + "/") for p in TOKEN_ALLOWED_PATHS
        ):
            raise PermissionDenied("An access token can't be used for this.")
        return user_for_api_token(token), {"access_token": True}  # nosec B105

    def authenticate_header(self, request) -> str:
        return "Bearer"


class FirebaseAuthentication(BaseAuthentication):
    """Authenticates `Authorization: Bearer <Firebase ID token>` on every request."""

    def authenticate(self, request):
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme != "Bearer" or not token:
            return None
        if not settings.FIREBASE_PROJECT_ID:
            logger.error("Sign-in attempted but FIREBASE_PROJECT_ID is not set")
            raise AuthenticationFailed("Sign-in isn't set up on this server yet.")
        try:
            claims = verify_firebase_token(token)
        except ValueError as error:
            raise AuthenticationFailed(
                "Your sign-in has expired. Sign in again."
            ) from error
        return user_for_token(claims), claims

    def authenticate_header(self, request) -> str:
        return "Bearer"


class DevAuthentication(BaseAuthentication):
    """Local development only: requests without a token sign in as DEV_AUTH_EMAIL.

    Lets the web app call the API before Firebase sign-in is wired up. Settings only
    install it when DJANGO_DEBUG is on.
    """

    def authenticate(self, request):
        email = settings.DEV_AUTH_EMAIL
        if not email or request.headers.get("Authorization"):
            return None
        claims = dev_user_claims(email)
        return user_for_token(claims), claims
