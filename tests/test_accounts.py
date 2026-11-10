import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from apps.accounts.auth import DevAuthentication
from apps.accounts.models import ALL_PERMISSIONS, MEMBER_PERMISSIONS, User
from apps.accounts.services import user_for_token
from tests.conftest import client_for, make_user


def claims(email, uid="uid-new", verified=True):
    return {
        "uid": uid,
        "email": email,
        "email_verified": verified,
        "name": "New Person",
    }


def test_first_sign_in_joins_the_company_for_the_email_domain(company):
    user = user_for_token(claims("New.Person@ACME.com"))

    assert user.org == company
    assert set(user.permissions) == MEMBER_PERMISSIONS
    assert user.email == "new.person@acme.com"


def test_sign_in_is_idempotent(company):
    first = user_for_token(claims("new@acme.com"))
    again = user_for_token(claims("new@acme.com"))

    assert first.id == again.id
    assert User.objects.count() == 1


def test_sign_in_without_a_company_leaves_the_person_unassigned(db):
    user = user_for_token(claims("someone@startup.io"))

    assert user.org is None
    response = client_for(user).get("/api/me")
    assert response.status_code == 200
    assert response.json()["org"] is None


def test_unverified_email_is_rejected(company):
    with pytest.raises(AuthenticationFailed):
        user_for_token(claims("new@acme.com", verified=False))


def test_request_without_a_token_is_unauthenticated(db, client):
    assert client.get("/api/me").status_code == 401


def test_person_without_a_company_cannot_see_lunches(db):
    user = make_user(None, "Solo")

    assert client_for(user).get("/api/lunches").status_code == 403


def test_setting_up_a_company_gives_the_founder_every_permission(db):
    founder = user_for_token(claims("founder@startup.io"))

    response = client_for(founder).post(
        "/api/company", {"name": "Startup"}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["email_domain"] == "startup.io"
    founder.refresh_from_db()
    assert set(founder.permissions) == ALL_PERMISSIONS
    teammate = user_for_token(claims("teammate@startup.io", uid="uid-teammate"))
    assert teammate.org_id == founder.org_id


def test_gmail_company_accepts_gmail_members(db):
    founder = user_for_token(claims("founder@gmail.com"))
    response = client_for(founder).post(
        "/api/company", {"name": "Gmail team"}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["email_domain"] == "gmail.com"
    member = user_for_token(claims("member@gmail.com", uid="uid-gmail-member"))
    assert member.org_id == founder.org_id


def test_only_the_company_email_domain_joins(company):
    # The fixture's company is acme.com; a Gmail address does not join it.
    member = user_for_token(claims("member@gmail.com"))

    assert member.org is None


def test_dev_sign_in_acts_as_the_configured_person(company, settings):
    settings.DEV_AUTH_EMAIL = "dev@acme.com"

    user, _ = DevAuthentication().authenticate(APIRequestFactory().get("/api/me"))

    assert user.email == "dev@acme.com"
    assert user.org == company


def test_dev_sign_in_steps_aside_for_a_real_token(db, settings):
    settings.DEV_AUTH_EMAIL = "dev@acme.com"
    request = APIRequestFactory().get("/api/me", HTTP_AUTHORIZATION="Bearer real-token")

    assert DevAuthentication().authenticate(request) is None


@pytest.fixture
def firebase_project(settings):
    settings.FIREBASE_PROJECT_ID = "lunchclub-test"
    return settings.FIREBASE_PROJECT_ID


def google_says(monkeypatch, claims=None, error=None):
    """Stand in for Google's check of a token's signature."""
    from google.oauth2 import id_token

    def verify(token, request, audience=None, clock_skew_in_seconds=0):
        if error:
            raise error
        return claims

    monkeypatch.setattr(id_token, "verify_firebase_token", verify)


def signed_in_get(client, path="/api/me"):
    return client.get(path, HTTP_AUTHORIZATION="Bearer a-firebase-token")


def test_a_google_sign_in_token_signs_the_person_in(
    company, client, firebase_project, monkeypatch
):
    google_says(
        monkeypatch,
        {
            "iss": f"https://securetoken.google.com/{firebase_project}",
            "sub": "firebase-uid-1",
            "email": "teammate@acme.com",
            "email_verified": True,
            "name": "Team Mate",
        },
    )

    response = signed_in_get(client)

    assert response.status_code == 200
    assert response.json()["email"] == "teammate@acme.com"
    assert response.json()["org"]["id"] == str(company.id)
    assert User.objects.get(firebase_uid="firebase-uid-1").name == "Team Mate"


def test_a_token_from_another_firebase_project_is_refused(
    db, client, firebase_project, monkeypatch
):
    google_says(
        monkeypatch,
        {
            "iss": "https://securetoken.google.com/someone-else",
            "sub": "firebase-uid-1",
            "email": "teammate@acme.com",
            "email_verified": True,
        },
    )

    assert signed_in_get(client).status_code == 401


def test_an_expired_or_forged_token_is_refused(
    db, client, firebase_project, monkeypatch
):
    google_says(monkeypatch, error=ValueError("Token expired"))

    response = signed_in_get(client)

    assert response.status_code == 401
    assert response.json()["detail"] == "Your sign-in has expired. Sign in again."


def test_sign_in_needs_the_firebase_project_configured(db, client, settings):
    settings.FIREBASE_PROJECT_ID = ""

    assert signed_in_get(client).status_code == 401
