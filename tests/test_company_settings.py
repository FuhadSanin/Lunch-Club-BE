import pytest
from django.core import mail

from apps.accounts.models import Address, Invite
from apps.accounts.services import user_for_token
from tests.conftest import client_for, make_user

URL = "/api/company/settings"


def claims(email, uid):
    return {"uid": uid, "email": email, "email_verified": True, "name": "X"}


def test_everyone_sees_the_company_settings(company, member):
    Address.objects.create(org=company, address="4th floor")

    body = client_for(member).get(URL).json()

    assert body["name"] == "Acme"
    assert "radius_km" not in body and "restaurant_mode" not in body
    assert [a["address"] for a in body["addresses"]] == ["4th floor"]


def test_the_host_changes_the_company_settings(admin):
    response = client_for(admin).patch(
        URL,
        {"name": "Acme Inc", "team_name": "Platform", "auto_join": False},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["team_name"], body["auto_join"]) == (
        "Acme Inc",
        "Platform",
        False,
    )


def test_settings_need_company_update(member):
    response = client_for(member).patch(URL, {"name": "Mine"}, format="json")

    assert response.status_code == 403


def test_the_email_domain_cannot_change(admin, company):
    client_for(admin).patch(URL, {"email_domain": "evil.com"}, format="json")

    company.refresh_from_db()
    assert company.email_domain == "acme.com"


@pytest.mark.parametrize(
    "body",
    [{"name": ""}],
)
def test_settings_are_validated(admin, body):
    response = client_for(admin).patch(URL, body, format="json")

    assert response.status_code == 400


def test_addresses_are_added_and_removed(admin, company):
    client = client_for(admin)

    added = client.post("/api/company/addresses", {"address": "Tower B"}, format="json")
    duplicate = client.post(
        "/api/company/addresses", {"address": "tower b"}, format="json"
    )
    removed = client.delete(f"/api/company/addresses/{added.json()['id']}")

    assert added.status_code == 201
    assert duplicate.status_code == 400
    assert removed.status_code == 204
    assert not Address.objects.filter(org=company).exists()


def test_an_address_keeps_where_it_is(admin, company):
    client = client_for(admin)

    added = client.post(
        "/api/company/addresses",
        {"address": "Tower B", "lat": 10.0, "lng": 76.3},
        format="json",
    )

    assert (added.json()["lat"], added.json()["lng"]) == (10.0, 76.3)
    listed = client.get(URL).json()["addresses"]
    assert [(a["address"], a["lat"], a["lng"]) for a in listed] == [
        ("Tower B", 10.0, 76.3)
    ]


@pytest.mark.parametrize(
    "where",
    [{"lat": 10.0}, {"lng": 76.3}, {"lat": 95, "lng": 10}],
    ids=["no longitude", "no latitude", "out of range"],
)
def test_an_address_needs_a_real_point_or_none(admin, where):
    response = client_for(admin).post(
        "/api/company/addresses", {"address": "Tower B", **where}, format="json"
    )

    assert response.status_code == 400


def test_an_address_a_lunch_went_to_leaves_the_list_but_not_the_lunch(
    admin, company, lunch
):
    address = lunch.address
    address.saved = True
    address.save()

    removed = client_for(admin).delete(f"/api/company/addresses/{address.id}")

    assert removed.status_code == 204
    assert client_for(admin).get(URL).json()["addresses"] == []
    lunch.refresh_from_db()
    assert lunch.address == address
    # Adding it again puts it back on the list.
    again = client_for(admin).post(
        "/api/company/addresses", {"address": address.address}, format="json"
    )
    assert again.status_code == 201
    assert again.json()["id"] == str(address.id)


def test_another_companys_address_is_out_of_reach(admin, other_company):
    theirs = Address.objects.create(org=other_company, address="Theirs")

    response = client_for(admin).delete(f"/api/company/addresses/{theirs.id}")

    assert response.status_code == 404
    assert Address.objects.filter(id=theirs.id).exists()


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

    dev = User.objects.get(email="dev@devco.io")
    assert set(dev.permissions) == ALL_PERMISSIONS
    assert [r.name for r in dev.roles.all()] == ["Host"]


def test_the_dev_company_records_who_set_it_up(db, settings):
    from django.core.management import call_command

    from apps.accounts.models import Organization, User

    settings.DEV_AUTH_EMAIL = "dev@devco.io"

    call_command("dev_company")

    org = Organization.objects.get(email_domain="devco.io")
    assert org.created_by == User.objects.get(email="dev@devco.io")
