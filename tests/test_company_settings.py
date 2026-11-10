import pytest
from django.core import mail

from apps.accounts.models import Invite, OfficeAddress
from apps.accounts.services import user_for_token
from tests.conftest import client_for, make_user

URL = "/api/company/settings"


def claims(email, uid):
    return {"uid": uid, "email": email, "email_verified": True, "name": "X"}


def test_everyone_sees_the_company_settings(company, member):
    OfficeAddress.objects.create(org=company, address="4th floor")

    body = client_for(member).get(URL).json()

    assert body["name"] == "Acme"
    assert body["radius_km"] == 3.0
    assert body["restaurant_mode"] == "nearby"
    assert [a["address"] for a in body["addresses"]] == ["4th floor"]


def test_the_host_changes_the_lunch_defaults(admin):
    response = client_for(admin).patch(
        URL,
        {
            "name": "Acme Inc",
            "team_name": "Platform",
            "radius_km": 5,
            "restaurant_mode": "manual",
            "auto_join": False,
            "office_lat": 10.0,
            "office_lng": 76.3,
        },
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["radius_km"], body["restaurant_mode"]) == (
        "Acme Inc",
        5.0,
        "manual",
    )
    assert body["auto_join"] is False
    assert body["office_lat"] == 10.0


def test_settings_need_company_update(member):
    response = client_for(member).patch(URL, {"name": "Mine"}, format="json")

    assert response.status_code == 403


def test_the_email_domain_cannot_change(admin, company):
    client_for(admin).patch(URL, {"email_domain": "evil.com"}, format="json")

    company.refresh_from_db()
    assert company.email_domain == "acme.com"


@pytest.mark.parametrize(
    "body",
    [
        {"radius_km": 20},
        {"radius_km": 0.1},
        {"name": ""},
        {"office_lat": 95, "office_lng": 10},
        {"office_lat": 10},  # latitude without longitude
    ],
)
def test_settings_are_validated(admin, body):
    response = client_for(admin).patch(URL, body, format="json")

    assert response.status_code == 400


def test_office_addresses_are_added_and_removed(admin, company):
    client = client_for(admin)

    added = client.post("/api/company/addresses", {"address": "Tower B"}, format="json")
    duplicate = client.post(
        "/api/company/addresses", {"address": "tower b"}, format="json"
    )
    removed = client.delete(f"/api/company/addresses/{added.json()['id']}")

    assert added.status_code == 201
    assert duplicate.status_code == 400
    assert removed.status_code == 204
    assert not OfficeAddress.objects.filter(org=company).exists()


def test_another_companys_address_is_out_of_reach(admin, other_company):
    theirs = OfficeAddress.objects.create(org=other_company, address="Theirs")

    response = client_for(admin).delete(f"/api/company/addresses/{theirs.id}")

    assert response.status_code == 404
    assert OfficeAddress.objects.filter(id=theirs.id).exists()


def test_invites_are_emailed_and_listed(admin, django_capture_on_commit_callbacks):
    client = client_for(admin)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            "/api/company/invites",
            {"emails": ["New@acme.com", "other@acme.com", "new@acme.com"]},
            format="json",
        )

    assert response.status_code == 201
    assert [m.to for m in mail.outbox] == [["new@acme.com"], ["other@acme.com"]]
    listed = client.get("/api/company/invites").json()
    assert {i["email"] for i in listed} == {"new@acme.com", "other@acme.com"}
    assert listed[0]["invited_by"] == "Asha"
    assert listed[0]["accepted"] is False


def test_only_the_company_domain_can_be_invited(admin):
    response = client_for(admin).post(
        "/api/company/invites", {"emails": ["x@gmail.com"]}, format="json"
    )

    assert response.status_code == 400
    assert not Invite.objects.exists()


def test_people_already_in_the_company_are_not_invited(admin, member):
    response = client_for(admin).post(
        "/api/company/invites", {"emails": [member.email]}, format="json"
    )

    assert response.status_code == 201
    assert response.json() == []


def test_with_auto_join_off_only_invited_people_join(admin, company):
    company.auto_join = False
    company.save()
    client_for(admin).post(
        "/api/company/invites", {"emails": ["in@acme.com"]}, format="json"
    )

    outsider = user_for_token(claims("out@acme.com", "uid-out"))
    invited = user_for_token(claims("in@acme.com", "uid-in"))

    assert outsider.org is None
    assert invited.org == company
    assert Invite.objects.get(email="in@acme.com").accepted_at is not None


def test_an_invite_is_withdrawn(admin, company):
    invite = Invite.objects.create(org=company, email="gone@acme.com")

    response = client_for(admin).delete(f"/api/company/invites/{invite.id}")

    assert response.status_code == 204
    assert not Invite.objects.exists()


def test_a_member_lists_the_company_and_registers_a_device(company, member):
    make_user(company, "Zed")
    client = client_for(member)

    members = client.get("/api/company/members").json()
    pushed = client.post("/api/me/push-token", {"token": "device-1"}, format="json")

    assert [m["name"] for m in members] == ["Meera", "Zed"]
    assert pushed.status_code == 204


def test_a_company_cannot_be_set_up_twice(admin):
    response = client_for(admin).post("/api/company", {"name": "Again"}, format="json")

    assert response.status_code == 409


def test_the_dev_company_command_sets_up_the_dev_person(db, settings):
    from django.core.management import call_command

    call_command("dev_company")  # nothing to do without DEV_AUTH_EMAIL
    settings.DEV_AUTH_EMAIL = "dev@devco.io"

    call_command("dev_company")
    call_command("dev_company")

    from apps.accounts.models import ALL_PERMISSIONS, User

    assert set(User.objects.get(email="dev@devco.io").permissions) == ALL_PERMISSIONS
