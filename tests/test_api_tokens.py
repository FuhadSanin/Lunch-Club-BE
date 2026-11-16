"""Access tokens: how an assistant that signed in acts as one person (MCP server)."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import ApiToken
from tests.conftest import client_for


def make_token(user, name="Claude", days=90):
    """The token an assistant is given when its person presses Allow (apps/oauth)."""
    row, token = services.issue_api_token(user, name, timedelta(days=days))
    return {
        "id": str(row.id),
        "name": row.name,
        "prefix": row.prefix,
        "expires_at": row.expires_at,
        "token": token,
    }


def with_token(token) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def test_a_token_is_shown_once_and_only_its_hash_is_kept(member):
    made = make_token(member, "Claude on my laptop")

    assert made["token"].startswith("lc_")
    assert made["prefix"] == made["token"][:11]
    assert made["expires_at"]
    row = ApiToken.objects.get()
    assert made["token"] not in {row.token_hash, row.prefix, row.name}
    assert len(row.token_hash) == 64
    listed = client_for(member).get("/api/me/tokens").json()
    assert [t["name"] for t in listed] == ["Claude on my laptop"]
    assert "token" not in listed[0]


def test_the_token_signs_in_as_its_person(member, lunch):
    token = make_token(member)["token"]

    me = with_token(token).get("/api/me")

    assert me.status_code == 200
    assert me.json()["email"] == member.email
    assert with_token(token).get("/api/lunches").status_code == 200


def test_using_a_token_is_recorded(member):
    made = make_token(member)

    with_token(made["token"]).get("/api/me")

    assert ApiToken.objects.get().last_used_at is not None


def test_an_unknown_token_is_refused(db):
    response = with_token("lc_" + "x" * 43).get("/api/me")

    assert response.status_code == 401
    assert "isn't valid" in response.json()["detail"]


def test_a_revoked_token_stops_working(member):
    made = make_token(member)

    revoked = client_for(member).delete(f"/api/me/tokens/{made['id']}")

    assert revoked.status_code == 204
    assert with_token(made["token"]).get("/api/me").status_code == 401
    assert client_for(member).get("/api/me/tokens").json() == []


def test_an_expired_token_stops_working(member):
    made = make_token(member)
    ApiToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

    response = with_token(made["token"]).get("/api/me")

    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


def test_someone_cannot_revoke_another_persons_token(member, host):
    made = make_token(member)

    response = client_for(host).delete(f"/api/me/tokens/{made['id']}")

    assert response.status_code == 404
    assert with_token(made["token"]).get("/api/me").status_code == 200


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/company/settings"),
        ("get", "/api/company/members"),
        ("get", "/api/permissions"),
        ("post", "/api/company"),
        ("post", "/api/me/push-token"),
    ],
)
def test_a_token_cannot_reach_company_admin_or_device_endpoints(admin, method, path):
    token = make_token(admin)["token"]

    response = getattr(with_token(token), method)(path)

    assert response.status_code == 403


def test_a_token_cannot_list_or_revoke_tokens(member):
    made = make_token(member)
    client = with_token(made["token"])

    assert client.get("/api/me/tokens").status_code in (401, 403)
    assert client.delete(f"/api/me/tokens/{made['id']}").status_code in (401, 403)
    assert ApiToken.objects.filter(revoked_at__isnull=True).count() == 1


def test_tokens_need_a_sign_in(db, client):
    assert client.get("/api/me/tokens").status_code == 401


def test_there_is_no_way_to_make_a_token_by_hand(member):
    """The one way to connect an assistant is for it to sign in."""
    response = client_for(member).post(
        "/api/me/tokens", {"name": "mine", "days": 30}, format="json"
    )

    assert response.status_code == 405
    assert not ApiToken.objects.exists()
