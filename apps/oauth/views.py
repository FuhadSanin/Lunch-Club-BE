from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.html import escape
from rest_framework.parsers import FormParser, JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.auth import DevAuthentication, FirebaseAuthentication
from apps.core.throttling import PublicThrottle
from apps.oauth import services
from apps.oauth.services import SCOPE, OAuthError


def issuer(request) -> str:
    """This API's public address: what assistants know it as."""
    return (settings.API_PUBLIC_URL or request.build_absolute_uri("/")).rstrip("/")


class PublicView(APIView):
    """An endpoint anyone can reach: no sign-in, counted per address."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicThrottle]
    parser_classes = [FormParser, JSONParser]  # the token endpoint takes a form

    def handle_exception(self, exc):
        if isinstance(exc, OAuthError):
            return self.oauth_error(exc)
        return super().handle_exception(exc)

    @staticmethod
    def oauth_error(exc: OAuthError) -> Response:
        response = Response(
            {"error": exc.error, "error_description": exc.description},
            status=exc.status,
        )
        response["Cache-Control"] = "no-store"
        return response


class MetadataView(PublicView):
    """GET /.well-known/oauth-authorization-server (RFC 8414): how to sign in here."""

    throttle_scope = "oauth_authorize"

    def get(self, request):
        base = issuer(request)
        return Response(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/oauth/authorize",
                "token_endpoint": f"{base}/oauth/token",
                "registration_endpoint": f"{base}/oauth/register",
                "revocation_endpoint": f"{base}/oauth/revoke",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": [SCOPE],
            }
        )


class RegisterView(PublicView):
    """POST /oauth/register (RFC 7591): an assistant says who it is, where to return."""

    throttle_scope = "oauth_register"

    def post(self, request):
        client = services.register_client(
            request.data.get("client_name", ""), request.data.get("redirect_uris")
        )
        return Response(
            {
                "client_id": client.client_id,
                "client_name": client.name,
                "redirect_uris": client.redirect_uris,
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",  # nosec B105 (the standard's "no client secret")
                "scope": SCOPE,
            },
            status=201,
        )


def _page(message: str, status: int = 400) -> HttpResponse:
    """A plain page for a problem the person must see here: the request can't be trusted
    enough to send them back to the app."""
    return HttpResponse(
        f"<!doctype html><meta charset=utf-8><title>Tiffmate</title>"
        "<body style='font:16px system-ui;max-width:32rem;margin:4rem auto'>"
        f"<h1>Can't sign in</h1><p>{escape(message)}</p>",
        status=status,
    )


class AuthorizeView(PublicView):
    """GET /oauth/authorize: the assistant sends the person here. Nothing is decided
    here: we keep the request and send them to the web app to sign in and say yes."""

    throttle_scope = "oauth_authorize"

    def get(self, request):
        q = request.query_params
        redirect_uri = q.get("redirect_uri", "")
        try:
            pending = services.start_request(
                services.Ask(
                    client_id=q.get("client_id", ""),
                    redirect_uri=redirect_uri,
                    code_challenge=q.get("code_challenge", ""),
                    method=q.get("code_challenge_method", ""),
                    state=q.get("state", ""),
                    scope=q.get("scope", ""),
                    resource=q.get("resource", ""),
                )
            )
        except OAuthError as error:
            if error.error in ("invalid_client", "invalid_redirect_uri"):
                return _page(
                    error.description
                )  # never send them to an unproven address
            return HttpResponseRedirect(
                services._back(
                    redirect_uri,
                    error=error.error,
                    error_description=error.description,
                    state=q.get("state", ""),
                )
            )
        if q.get("response_type") != "code":
            return HttpResponseRedirect(
                services._back(
                    redirect_uri,
                    error="unsupported_response_type",
                    state=q.get("state", ""),
                )
            )
        return HttpResponseRedirect(
            f"{settings.APP_URL.rstrip('/')}/#/authorize/{pending.id}"
        )


class TokenView(PublicView):
    """POST /oauth/token: trade a code for tokens, or renew with a refresh token."""

    throttle_scope = "oauth_token"

    def post(self, request):
        d = request.data
        kind = d.get("grant_type")
        if kind == "authorization_code":
            tokens = services.exchange_code(
                d.get("client_id", ""),
                d.get("code", ""),
                d.get("redirect_uri", ""),
                d.get("code_verifier", ""),
            )
        elif kind == "refresh_token":
            tokens = services.refresh(
                d.get("client_id", ""), d.get("refresh_token", "")
            )
        else:
            raise OAuthError(
                "unsupported_grant_type", "Use authorization_code or refresh_token."
            )
        response = Response(tokens)
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        return response


class RevokeView(PublicView):
    """POST /oauth/revoke (RFC 7009): the assistant signs itself out."""

    throttle_scope = "oauth_token"

    def post(self, request):
        services.revoke(
            request.data.get("client_id", ""), request.data.get("token", "")
        )
        return Response(status=200)


# ---- the consent page's API: needs the person's own sign-in, not an access token ----


class SignedInView(APIView):
    authentication_classes = [FirebaseAuthentication, DevAuthentication]

    def handle_exception(self, exc):
        if isinstance(exc, OAuthError):
            return Response({"detail": exc.description}, status=exc.status)
        return super().handle_exception(exc)


class RequestView(SignedInView):
    """GET /api/oauth/requests/<id>: who is asking, so the person can decide."""

    def get(self, request, request_id):
        found = services.pending_request(request_id)
        host = urlsplit(found.redirect_uri)
        shown = host.hostname or ""
        if host.port:
            shown += f":{host.port}"
        return Response(
            {
                "client_name": found.client.name,
                "redirect_host": shown,
                "expires_at": found.expires_at,
            }
        )


class ApproveView(SignedInView):
    """POST /api/oauth/requests/<id>/approve: yes. The answer says where to send the
    browser (back to the assistant, with the one-time code)."""

    def post(self, request, request_id):
        return Response({"redirect_to": services.approve(request_id, request.user)})


class DenyView(SignedInView):
    """POST /api/oauth/requests/<id>/deny: no."""

    def post(self, request, request_id):
        return Response({"redirect_to": services.deny(request_id)})
