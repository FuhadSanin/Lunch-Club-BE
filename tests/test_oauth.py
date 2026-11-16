"""Signing in an AI assistant: register, send the person to consent, trade the code
(with PKCE) for tokens, renew, and the ways it must refuse."""

import base64
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.core.cache import caches
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ApiToken
from apps.oauth.models import OAuthClient, OAuthRefreshToken, OAuthRequest
from tests.conftest import client_for
from tests.test_api_tokens import with_token

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 43 + "-._~abc"  # any 43 to 128 unreserved characters


def challenge_for(verifier=VERIFIER):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@pytest.fixture(autouse=True)
def fresh_counts():
    caches["throttle"].clear()


NOT_GIVEN = object()


def register(name="Claude", uris=NOT_GIVEN, client=None):
    client = client or APIClient()
    return client.post(
        "/oauth/register",
        {
            "client_name": name,
            "redirect_uris": [REDIRECT] if uris is NOT_GIVEN else uris,
        },
        format="json",
    )


def form(client, path, data):
    """A POST the way the standard says: application/x-www-form-urlencoded."""
    return client.post(
        path, urlencode(data), content_type="application/x-www-form-urlencoded"
    )


def authorize(client_id, **overrides):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge_for(),
        "code_challenge_method": "S256",
        "state": "xyz",
        **overrides,
    }
    return APIClient().get("/oauth/authorize", params)


def sign_in(user, **overrides):
    """The whole round trip but the token exchange: returns (client_id, code)."""
    client_id = register().json()["client_id"]
    sent = authorize(client_id, **overrides)
    assert sent.status_code == 302, sent.content
    request_id = urlsplit(sent["Location"]).fragment.rsplit("/", 1)[1]
    approved = client_for(user).post(f"/api/oauth/requests/{request_id}/approve")
    assert approved.status_code == 200
    back = parse_qs(urlsplit(approved.json()["redirect_to"]).query)
    return client_id, back["code"][0], back, request_id


def trade(client_id, code, verifier=VERIFIER, redirect_uri=REDIRECT):
    return form(
        APIClient(),
        "/oauth/token",
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
    )


# ---- discovery and registration ----


def test_the_sign_in_endpoints_are_advertised(db):
    meta = APIClient().get("/.well-known/oauth-authorization-server").json()

    assert meta["authorization_endpoint"].endswith("/oauth/authorize")
    assert meta["token_endpoint"].endswith("/oauth/token")
    assert meta["registration_endpoint"].endswith("/oauth/register")
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["token_endpoint_auth_methods_supported"] == ["none"]
    assert meta["scopes_supported"] == ["lunch"]


def test_the_issuer_can_be_set_for_a_public_address(db, settings):
    settings.API_PUBLIC_URL = "https://api.example/"

    meta = APIClient().get("/.well-known/oauth-authorization-server").json()

    assert meta["issuer"] == "https://api.example"
    assert meta["token_endpoint"] == "https://api.example/oauth/token"


def test_an_app_registers_itself(db):
    response = register("Claude Desktop")

    assert response.status_code == 201
    body = response.json()
    assert body["client_name"] == "Claude Desktop"
    assert body["redirect_uris"] == [REDIRECT]
    assert OAuthClient.objects.get(client_id=body["client_id"]).name == "Claude Desktop"


@pytest.mark.parametrize(
    "uris",
    [
        ["http://evil.example/cb"],  # http is only for the person's own machine
        ["javascript:alert(1)"],
        ["https://user:pw@claude.ai/cb"],
        ["https://claude.ai/cb#frag"],
        ["https://claude.ai/" + "x" * 300],
        [],
        ["https://a.example/1"] * 6,
        "https://claude.ai/cb",
        None,
    ],
)
def test_unsafe_redirect_addresses_are_refused(db, uris):
    response = register(uris=uris)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_loopback_http_is_allowed_for_apps_on_the_persons_machine(db):
    ok = register(uris=["http://localhost:33418/callback", "http://127.0.0.1:8080/cb"])

    assert ok.status_code == 201


def test_a_name_is_required_and_cleaned(db):
    assert register(name="  ").status_code == 400
    cleaned = register(name="<script>alert(1)</script>Claude").json()["client_name"]
    assert "<" not in cleaned and ">" not in cleaned


def test_registration_is_limited_per_address(db, monkeypatch):
    from rest_framework.throttling import SimpleRateThrottle

    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "oauth_register", "2/hour")
    client = APIClient()

    codes = [register(client=client).status_code for _ in range(3)]

    assert codes == [201, 201, 429]
    other = APIClient()
    assert register(client=other, name="Other").status_code in (201, 429)


# ---- the sign-in itself ----


def test_a_whole_sign_in_gives_the_assistant_tokens_that_act_as_the_person(member):
    client_id, code, back, _ = sign_in(member)
    assert back["state"] == ["xyz"]  # handed back untouched

    response = trade(client_id, code)

    assert response.status_code == 200
    tokens = response.json()
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 3600
    assert tokens["scope"] == "lunch"
    assert tokens["access_token"].startswith("lc_")
    assert tokens["refresh_token"].startswith("lr_")
    assert response["Cache-Control"] == "no-store"
    me = with_token(tokens["access_token"]).get("/api/me")
    assert me.status_code == 200 and me.json()["email"] == member.email


def test_the_authorize_endpoint_sends_the_person_to_the_web_app(db, settings):
    settings.APP_URL = "https://app.example"
    client_id = register().json()["client_id"]

    sent = authorize(client_id)

    assert sent.status_code == 302
    assert sent["Location"].startswith("https://app.example/#/authorize/")
    assert OAuthRequest.objects.count() == 1


def test_the_consent_page_says_who_is_asking(member):
    client_id = register("Claude", [REDIRECT]).json()["client_id"]
    request_id = urlsplit(authorize(client_id)["Location"]).fragment.split("/")[-1]

    shown = client_for(member).get(f"/api/oauth/requests/{request_id}")

    assert shown.status_code == 200
    assert shown.json()["client_name"] == "Claude"
    assert shown.json()["redirect_host"] == "claude.ai"


def test_saying_no_sends_the_assistant_an_error(member):
    client_id = register().json()["client_id"]
    request_id = urlsplit(authorize(client_id)["Location"]).fragment.split("/")[-1]

    denied = client_for(member).post(f"/api/oauth/requests/{request_id}/deny")

    back = parse_qs(urlsplit(denied.json()["redirect_to"]).query)
    assert back["error"] == ["access_denied"] and back["state"] == ["xyz"]
    assert "code" not in back
    answered = client_for(member).post(f"/api/oauth/requests/{request_id}/approve")
    assert answered.status_code == 410


def test_a_sign_in_can_be_answered_only_once(member, host):
    _, _, _, request_id = sign_in(member)

    again = client_for(host).post(f"/api/oauth/requests/{request_id}/approve")

    assert again.status_code == 410
    assert client_for(host).get(f"/api/oauth/requests/{request_id}").status_code == 410


def test_a_sign_in_not_answered_in_time_expires(member):
    client_id = register().json()["client_id"]
    request_id = urlsplit(authorize(client_id)["Location"]).fragment.split("/")[-1]
    OAuthRequest.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    assert (
        client_for(member).get(f"/api/oauth/requests/{request_id}").status_code == 410
    )
    assert (
        client_for(member).post(f"/api/oauth/requests/{request_id}/approve").status_code
        == 410
    )


def test_an_unknown_request_is_not_found(member):
    unknown = "123e4567-e89b-42d3-a456-426614174000"

    assert client_for(member).get(f"/api/oauth/requests/{unknown}").status_code == 404


def test_only_a_real_sign_in_can_answer_not_an_access_token(member):
    from tests.test_api_tokens import make_token

    client_id = register().json()["client_id"]
    request_id = urlsplit(authorize(client_id)["Location"]).fragment.split("/")[-1]
    token = make_token(member)["token"]

    response = with_token(token).post(f"/api/oauth/requests/{request_id}/approve")

    assert response.status_code in (401, 403)
    assert not OAuthRequest.objects.filter(approved_at__isnull=False).exists()


def test_the_consent_endpoints_need_a_sign_in(db):
    unknown = "123e4567-e89b-42d3-a456-426614174000"

    assert APIClient().post(f"/api/oauth/requests/{unknown}/approve").status_code == 401


# ---- the ways it must refuse ----


def test_an_unregistered_redirect_is_never_followed(member):
    client_id = register().json()["client_id"]

    refused = authorize(client_id, redirect_uri="https://evil.example/steal")

    assert refused.status_code == 400  # a page here, not a redirect to that address
    assert "Location" not in refused
    assert OAuthRequest.objects.count() == 0


def test_an_unknown_app_is_refused(db):
    assert authorize("nobody").status_code == 400


def test_pkce_is_required(db):
    client_id = register().json()["client_id"]

    no_challenge = authorize(client_id, code_challenge="")
    plain = authorize(client_id, code_challenge_method="plain")

    for refused in (no_challenge, plain):
        assert refused.status_code == 302  # back to the app, with the error
        assert parse_qs(urlsplit(refused["Location"]).query)["error"] == [
            "invalid_request"
        ]
    assert OAuthRequest.objects.count() == 0


def test_only_the_code_flow_and_the_lunch_scope_are_offered(db):
    client_id = register().json()["client_id"]

    bad_type = authorize(client_id, response_type="token")
    bad_scope = authorize(client_id, scope="admin")

    assert parse_qs(urlsplit(bad_type["Location"]).query)["error"] == [
        "unsupported_response_type"
    ]
    assert parse_qs(urlsplit(bad_scope["Location"]).query)["error"] == ["invalid_scope"]
    assert authorize(client_id, scope="lunch").status_code == 302


def test_the_wrong_verifier_gets_nothing(member):
    client_id, code, _, _ = sign_in(member)

    response = trade(client_id, code, verifier="w" * 50)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert not ApiToken.objects.exists()


def test_a_code_cannot_be_used_from_another_redirect(member):
    client_id, code, _, _ = sign_in(member)

    response = trade(client_id, code, redirect_uri="https://evil.example/cb")

    assert response.status_code == 400


def test_a_code_belongs_to_the_app_that_asked(member):
    _, code, _, _ = sign_in(member)
    other = register("Other").json()["client_id"]

    assert trade(other, code).status_code == 400


def test_a_code_works_once_and_using_it_twice_cancels_what_it_gave(member):
    client_id, code, _, _ = sign_in(member)
    first = trade(client_id, code).json()

    again = trade(client_id, code)

    assert again.status_code == 400
    assert again.json()["error_description"] == "That code was already used."
    # the copied code cancelled the tokens it had produced
    assert with_token(first["access_token"]).get("/api/me").status_code == 401
    assert refresh_with(client_id, first["refresh_token"]).status_code == 400


def test_a_code_expires_quickly(member):
    client_id, code, _, _ = sign_in(member)
    OAuthRequest.objects.update(code_expires_at=timezone.now() - timedelta(seconds=1))

    assert trade(client_id, code).status_code == 400


def test_garbage_and_unknown_grants_are_refused(db):
    client = APIClient()

    assert form(client, "/oauth/token", {"grant_type": "password"}).status_code == 400
    empty = form(client, "/oauth/token", {})
    assert empty.json()["error"] == "unsupported_grant_type"
    bad = form(
        client,
        "/oauth/token",
        {"grant_type": "authorization_code", "client_id": "x", "code": "nope"},
    )
    assert bad.json()["error"] == "invalid_grant"


def test_the_token_endpoint_also_takes_json(member):
    client_id, code, _, _ = sign_in(member)

    response = APIClient().post(
        "/oauth/token",
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
        },
        format="json",
    )

    assert response.status_code == 200


# ---- renewing ----


def refresh_with(client_id, token):
    return form(
        APIClient(),
        "/oauth/token",
        {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": token},
    )


def test_refreshing_replaces_both_tokens(member):
    client_id, code, _, _ = sign_in(member)
    first = trade(client_id, code).json()

    second = refresh_with(client_id, first["refresh_token"])

    assert second.status_code == 200
    renewed = second.json()
    assert renewed["access_token"] != first["access_token"]
    assert renewed["refresh_token"] != first["refresh_token"]
    assert (
        with_token(first["access_token"]).get("/api/me").status_code == 401
    )  # retired
    assert with_token(renewed["access_token"]).get("/api/me").status_code == 200


def test_a_refresh_token_used_twice_cancels_the_whole_line(member):
    client_id, code, _, _ = sign_in(member)
    first = trade(client_id, code).json()
    renewed = refresh_with(client_id, first["refresh_token"]).json()

    replay = refresh_with(client_id, first["refresh_token"])

    assert replay.status_code == 400
    assert with_token(renewed["access_token"]).get("/api/me").status_code == 401
    assert refresh_with(client_id, renewed["refresh_token"]).status_code == 400


def test_an_expired_refresh_token_is_refused(member):
    client_id, code, _, _ = sign_in(member)
    tokens = trade(client_id, code).json()
    OAuthRefreshToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    assert refresh_with(client_id, tokens["refresh_token"]).status_code == 400


def test_a_refresh_token_belongs_to_its_app(member):
    client_id, code, _, _ = sign_in(member)
    tokens = trade(client_id, code).json()
    other = register("Other").json()["client_id"]

    assert refresh_with(other, tokens["refresh_token"]).status_code == 400


def test_the_access_token_expires_by_itself(member):
    client_id, code, _, _ = sign_in(member)
    token = trade(client_id, code).json()["access_token"]
    ApiToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    assert with_token(token).get("/api/me").status_code == 401


# ---- what the assistant may do, and signing out ----


def test_the_assistants_token_has_the_same_limits_as_any_access_token(admin):
    client_id, code, _, _ = sign_in(admin)
    token = trade(client_id, code).json()["access_token"]
    assistant = with_token(token)

    assert assistant.get("/api/lunches").status_code == 200
    for path in ("/api/company/settings", "/api/company/members", "/api/permissions"):
        assert assistant.get(path).status_code == 403
    assert assistant.get("/api/me/tokens").status_code in (401, 403)


def test_signing_out_revokes_everything_the_app_had(member):
    client_id, code, _, _ = sign_in(member)
    tokens = trade(client_id, code).json()

    out = form(
        APIClient(),
        "/oauth/revoke",
        {"client_id": client_id, "token": tokens["refresh_token"]},
    )

    assert out.status_code == 200
    assert with_token(tokens["access_token"]).get("/api/me").status_code == 401
    assert refresh_with(client_id, tokens["refresh_token"]).status_code == 400


def test_signing_out_with_the_access_token_works_too(member):
    client_id, code, _, _ = sign_in(member)
    tokens = trade(client_id, code).json()

    form(
        APIClient(),
        "/oauth/revoke",
        {"client_id": client_id, "token": tokens["access_token"]},
    )

    assert with_token(tokens["access_token"]).get("/api/me").status_code == 401


def test_revoking_something_unknown_does_nothing_and_says_nothing(db):
    nothing = form(APIClient(), "/oauth/revoke", {"client_id": "x", "token": "nope"})
    assert nothing.status_code == 200


def test_the_person_can_end_a_connection_from_their_token_list(member):
    client_id, code, _, _ = sign_in(member)
    tokens = trade(client_id, code).json()
    listed = client_for(member).get("/api/me/tokens").json()

    assert [t["name"] for t in listed] == ["Claude"]
    client_for(member).delete(f"/api/me/tokens/{listed[0]['id']}")

    assert with_token(tokens["access_token"]).get("/api/me").status_code == 401


def test_disconnecting_from_the_list_also_ends_the_refresh_token(member):
    client_id, code, _, _ = sign_in(member)
    tokens = trade(client_id, code).json()
    listed = client_for(member).get("/api/me/tokens").json()

    client_for(member).delete(f"/api/me/tokens/{listed[0]['id']}")

    assert refresh_with(client_id, tokens["refresh_token"]).status_code == 400
    assert with_token(tokens["access_token"]).get("/api/me").status_code == 401
    assert client_for(member).get("/api/me/tokens").json() == []


def test_the_sign_in_endpoints_are_limited_per_address(db, monkeypatch):
    from rest_framework.throttling import SimpleRateThrottle

    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "oauth_token", "2/min")
    client = APIClient()

    codes = [
        form(client, "/oauth/token", {"grant_type": "x"}).status_code for _ in range(3)
    ]

    assert codes == [400, 400, 429]
