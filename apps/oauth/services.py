"""Signing in an AI assistant: OAuth 2.1 authorization code with PKCE (RFC 6749, 7636,
7591, 8252).

The assistant registers itself, sends the person to our consent page, and trades the
code it gets back for a short-lived access token (an ApiToken, with the limits every
access token has: lunches and notifications, never settings, people or tokens) and a
refresh token that is replaced each time it is used.
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts import services as accounts
from apps.accounts.models import ApiToken, User
from apps.oauth.models import OAuthClient, OAuthRefreshToken, OAuthRequest

SCOPE = "lunch"
LOOPBACK = {"localhost", "127.0.0.1", "[::1]", "::1"}
REFRESH_PREFIX = "lr_"
VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
CHALLENGE = re.compile(r"^[A-Za-z0-9\-_]{43}$")  # base64url of a SHA-256
NAME_OK = re.compile(r"[^\w .,'()&@+\-]")


class OAuthError(Exception):
    """An RFC 6749 error: `error` is the code, `description` says what to fix."""

    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid_redirect_uri(uri: str) -> bool:
    """https anywhere, or http only back to the person's own machine (a desktop app or
    CLI listening on localhost). No fragments, no credentials in the address."""
    if not isinstance(uri, str) or len(uri) > 300:
        return False
    parts = urlsplit(uri)
    if parts.fragment or parts.username or parts.password or not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in LOOPBACK


def register_client(name: str, redirect_uris: list) -> OAuthClient:
    """Dynamic client registration (RFC 7591): an assistant says who it is."""
    clean = NAME_OK.sub("", str(name or "")).strip()[:100]
    if not clean:
        raise OAuthError("invalid_client_metadata", "client_name is required.")
    if (
        not isinstance(redirect_uris, list)
        or not 1 <= len(redirect_uris) <= 5
        or not all(valid_redirect_uri(u) for u in redirect_uris)
    ):
        raise OAuthError(
            "invalid_redirect_uri",
            "Give 1 to 5 redirect_uris: https, or http to localhost.",
        )
    return OAuthClient.objects.create(
        client_id=secrets.token_urlsafe(18), name=clean, redirect_uris=redirect_uris
    )


@dataclass
class Ask:
    """What an assistant sent to /oauth/authorize."""

    client_id: str
    redirect_uri: str
    code_challenge: str
    method: str
    state: str = ""
    scope: str = ""
    resource: str = ""


def start_request(ask: Ask) -> OAuthRequest:
    """The assistant has sent the person here. Raises OAuthError; the caller decides
    whether to show it (a bad client or redirect) or send it back to the app."""
    client = OAuthClient.objects.filter(client_id=ask.client_id).first()
    if client is None:
        raise OAuthError("invalid_client", "Unknown client_id.")
    if ask.redirect_uri not in client.redirect_uris:
        raise OAuthError(
            "invalid_redirect_uri", "That redirect_uri isn't registered for this app."
        )
    if ask.method != "S256" or not CHALLENGE.match(ask.code_challenge or ""):
        raise OAuthError(
            "invalid_request", "PKCE is required: code_challenge with method S256."
        )
    if ask.scope and ask.scope.split() != [SCOPE]:
        raise OAuthError("invalid_scope", f"The only scope is '{SCOPE}'.")
    OAuthRequest.objects.filter(
        expires_at__lt=timezone.now() - timedelta(days=1)
    ).delete()  # old unfinished sign-ins
    return OAuthRequest.objects.create(
        client=client,
        redirect_uri=ask.redirect_uri,
        state=ask.state[:512],
        code_challenge=ask.code_challenge,
        scope=SCOPE,
        resource=ask.resource[:300],
        expires_at=timezone.now() + timedelta(minutes=settings.OAUTH_REQUEST_MINUTES),
    )


def pending_request(request_id) -> OAuthRequest:
    """A sign-in still waiting for the person's answer, or OAuthError (404/410)."""
    found = OAuthRequest.objects.select_related("client").filter(id=request_id).first()
    if found is None:
        raise OAuthError("not_found", "This sign-in link isn't valid.", 404)
    if found.approved_at or found.denied_at:
        raise OAuthError("gone", "This sign-in was already answered.", 410)
    if found.expires_at <= timezone.now():
        raise OAuthError(
            "gone", "This sign-in took too long. Start again in your assistant.", 410
        )
    return found


def _back(redirect_uri: str, **params: str) -> str:
    """The redirect_uri with the answer added to its query."""
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query) + [(k, v) for k, v in params.items() if v != ""]
    return urlunsplit(parts._replace(query=urlencode(query)))


def approve(request_id, user: User) -> str:
    """The person said yes: make the one-time code and say where to send the browser."""
    with transaction.atomic():
        req = (
            OAuthRequest.objects.select_for_update(of=("self",))
            .select_related("client")
            .filter(id=request_id)
            .first()
        )
        if req is None:
            raise OAuthError("not_found", "This sign-in link isn't valid.", 404)
        if req.approved_at or req.denied_at:
            raise OAuthError("gone", "This sign-in was already answered.", 410)
        if req.expires_at <= timezone.now():
            raise OAuthError(
                "gone",
                "This sign-in took too long. Start again in your assistant.",
                410,
            )
        code = secrets.token_urlsafe(32)
        req.user = user
        req.approved_at = timezone.now()
        req.code_hash = _hash(code)
        req.code_expires_at = timezone.now() + timedelta(
            seconds=settings.OAUTH_CODE_SECONDS
        )
        req.save()
    return _back(req.redirect_uri, code=code, state=req.state)


def deny(request_id) -> str:
    req = pending_request(request_id)
    req.denied_at = timezone.now()
    req.save(update_fields=["denied_at", "updated_at"])
    return _back(
        req.redirect_uri,
        error="access_denied",
        error_description="The person said no.",
        state=req.state,
    )


def _tokens(user: User, client: OAuthClient) -> dict:
    """A fresh access token and refresh token, as the token endpoint answers."""
    lifetime = timedelta(seconds=settings.OAUTH_ACCESS_TOKEN_SECONDS)
    access_row, access = accounts.issue_api_token(user, client.name, lifetime)
    refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
    OAuthRefreshToken.objects.create(
        user=user,
        client=client,
        token_hash=_hash(refresh),
        access_token=access_row,
        expires_at=timezone.now() + timedelta(days=settings.OAUTH_REFRESH_TOKEN_DAYS),
    )
    return {
        "access_token": access,
        "token_type": "Bearer",  # nosec B105 (the standard's token type)
        "expires_in": settings.OAUTH_ACCESS_TOKEN_SECONDS,
        "refresh_token": refresh,
        "scope": SCOPE,
    }


def _revoke_line(user: User, client: OAuthClient) -> None:
    """Everything this app has from this person: its refresh and access tokens."""
    now = timezone.now()
    lines = OAuthRefreshToken.objects.filter(user=user, client=client)
    ApiToken.objects.filter(
        id__in=lines.values("access_token_id"), revoked_at__isnull=True
    ).update(revoked_at=now)
    lines.filter(revoked_at__isnull=True).update(revoked_at=now)


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not VERIFIER.match(verifier or ""):
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(computed, challenge)


def exchange_code(
    client_id: str, code: str, redirect_uri: str, code_verifier: str
) -> dict:
    """The app trades its code (and proves it is the app that asked) for tokens.

    Whatever the outcome, a code that is looked at is spent, and a code used twice
    cancels
    what it gave out. Those writes must stay even when the answer is an error, so the
    error is raised only after the transaction has committed.
    """
    failure: OAuthError | None = None
    result: dict = {}
    with transaction.atomic():
        req = (
            OAuthRequest.objects.select_for_update(of=("self",))
            .select_related("client", "user")
            .filter(code_hash=_hash(code or ""))
            .first()
        )
        if req is None or req.user is None or req.client.client_id != client_id:
            failure = OAuthError("invalid_grant", "That code isn't valid.")
        elif req.code_used_at is not None:
            # A code used twice: someone copied it. Cancel what it already gave out.
            _revoke_line(req.user, req.client)
            failure = OAuthError("invalid_grant", "That code was already used.")
        else:
            req.code_used_at = timezone.now()
            req.save(update_fields=["code_used_at", "updated_at"])
            if req.code_expires_at is None or req.code_expires_at <= timezone.now():
                failure = OAuthError("invalid_grant", "That code has expired.")
            elif redirect_uri != req.redirect_uri:
                failure = OAuthError(
                    "invalid_grant", "redirect_uri doesn't match the request."
                )
            elif not _pkce_ok(code_verifier, req.code_challenge):
                failure = OAuthError(
                    "invalid_grant", "The code_verifier doesn't match."
                )
            else:
                result = _tokens(req.user, req.client)
    if failure:
        raise failure
    return result


def refresh(client_id: str, refresh_token: str) -> dict:
    """Renew: the old refresh token and access token are retired, new ones issued. A
    refresh token used twice cancels the whole line (committed before the error)."""
    failure: OAuthError | None = None
    result: dict = {}
    with transaction.atomic():
        row = (
            OAuthRefreshToken.objects.select_for_update(of=("self",))
            .select_related("client", "user", "access_token")
            .filter(token_hash=_hash(refresh_token or ""))
            .first()
        )
        now = timezone.now()
        if row is None or row.client.client_id != client_id:
            failure = OAuthError("invalid_grant", "That refresh token isn't valid.")
        elif row.used_at is not None:
            _revoke_line(row.user, row.client)  # used twice: it leaked
            failure = OAuthError(
                "invalid_grant", "That refresh token was already used."
            )
        elif row.revoked_at is not None or row.expires_at <= now:
            failure = OAuthError("invalid_grant", "That refresh token has expired.")
        else:
            row.used_at = now
            row.save(update_fields=["used_at", "updated_at"])
            if row.access_token_id:
                ApiToken.objects.filter(
                    id=row.access_token_id, revoked_at__isnull=True
                ).update(revoked_at=now)
            result = _tokens(row.user, row.client)
    if failure:
        raise failure
    return result


def disconnect(token: ApiToken) -> None:
    """The person ends an assistant's connection from their list: the access token and
    the refresh token behind it, so the assistant can't quietly renew itself. It ends
    everything that app has from this person."""
    link = (
        OAuthRefreshToken.objects.select_related("client", "user")
        .filter(access_token=token)
        .first()
    )
    if link is not None:
        _revoke_line(link.user, link.client)
    ApiToken.objects.filter(id=token.id, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def revoke(client_id: str, token: str) -> None:
    """RFC 7009: the app signs itself out. Quietly does nothing for an unknown token."""
    row = (
        OAuthRefreshToken.objects.select_related("client", "user")
        .filter(token_hash=_hash(token or ""))
        .first()
    )
    if row is not None and row.client.client_id == client_id:
        _revoke_line(row.user, row.client)
        return
    access = (
        ApiToken.objects.filter(token_hash=_hash(token or ""))
        .filter(name__isnull=False)
        .first()
    )
    if access is not None:
        link = (
            OAuthRefreshToken.objects.select_related("client", "user")
            .filter(access_token=access, client__client_id=client_id)
            .first()
        )
        if link is not None:
            _revoke_line(link.user, link.client)
