"""Roles and permissions: a role is a named set of permissions inside a company, and a
person's permissions are what their roles grant together."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts import rbac
from apps.accounts.models import (
    ALL_PERMISSIONS,
    MEMBER_PERMISSIONS,
    Permission,
    Role,
    RolePermission,
    User,
)
from apps.accounts.services import user_for_token
from tests.conftest import client_for, make_user

ROLES = "/api/company/roles"


def role_named(org, name):
    return Role.objects.get(org=org, name=name)


def make_role(org, name, permissions):
    role = Role.objects.create(org=org, name=name)
    RolePermission.objects.bulk_create(
        RolePermission(role=role, permission=p) for p in permissions
    )
    return role


def create(by, name, permissions):
    return client_for(by).post(
        ROLES, {"name": name, "permissions": sorted(permissions)}, format="json"
    )


def assign(by, person, *roles):
    return client_for(by).put(
        f"/api/company/members/{person.id}/roles",
        {"roles": [str(r.id) for r in roles]},
        format="json",
    )


def fresh(user):
    """A real request loads the person afresh."""
    return User.objects.get(id=user.id)


# --- the built-in roles ---------------------------------------------------------


def test_a_company_has_a_host_role_with_everything_and_a_member_role(company):
    roles = rbac.ensure_system_roles(company)
    rbac.ensure_system_roles(company)  # safe to repeat

    assert Role.objects.filter(org=company).count() == 2
    assert {p.permission for p in roles["Host"].grants.all()} == ALL_PERMISSIONS
    assert {p.permission for p in roles["Member"].grants.all()} == MEMBER_PERMISSIONS
    assert all(role.is_system for role in roles.values())


def test_the_founder_is_a_host_and_people_who_join_are_members(db):
    founder = user_for_token(
        {"uid": "f", "email": "f@startup.io", "email_verified": True, "name": "F"}
    )
    client_for(founder).post("/api/company", {"name": "Startup"}, format="json")
    teammate = user_for_token(
        {"uid": "t", "email": "t@startup.io", "email_verified": True, "name": "T"}
    )

    founder = fresh(founder)
    assert [r.name for r in founder.roles.all()] == ["Host"]
    assert [r.name for r in teammate.roles.all()] == ["Member"]
    assert set(founder.permissions) == ALL_PERMISSIONS
    assert set(teammate.permissions) == MEMBER_PERMISSIONS


def test_people_see_their_roles_with_their_permissions(member):
    me = client_for(member).get("/api/me").json()

    assert [r["name"] for r in me["roles"]] == ["Member"]
    assert me["permissions"] == sorted(MEMBER_PERMISSIONS)


def test_roles_are_listed_with_what_they_allow_and_who_has_them(admin, member):
    roles = client_for(member).get(ROLES).json()

    by_name = {r["name"]: r for r in roles}
    assert [r["name"] for r in roles] == ["Host", "Member"]
    assert by_name["Host"]["permissions"] == sorted(ALL_PERMISSIONS)
    assert by_name["Host"]["system"] is True
    assert by_name["Host"]["members"] == 1
    assert by_name["Member"]["permissions"] == sorted(MEMBER_PERMISSIONS)
    assert by_name["Member"]["members"] == 1


# --- making a role and giving it to someone --------------------------------------


def test_an_auditor_role_gives_exactly_what_it_grants(admin, company, member):
    audit = {Permission.SESSION_READ, Permission.MEMBER_READ}

    made = create(admin, "Auditor", audit)
    given = assign(admin, member, role_named(company, "Auditor"))

    assert made.status_code == 201
    assert made.json()["permissions"] == sorted(audit)
    assert given.status_code == 200
    assert [r["name"] for r in given.json()["roles"]] == ["Auditor"]
    assert given.json()["permissions"] == sorted(audit)
    # Priya can read, and can do nothing a member could (like picking food).
    priya = fresh(member)
    assert client_for(priya).get("/api/lunches").status_code == 200
    assert client_for(priya).get("/api/company/members").status_code == 200
    assert client_for(priya).get("/api/restaurants?lat=10&lng=76").status_code == 403


def test_a_person_with_two_roles_has_both(admin, company, member):
    create(admin, "Auditor", {Permission.COMPANY_UPDATE})

    response = assign(
        admin, member, role_named(company, "Member"), role_named(company, "Auditor")
    )

    assert response.json()["permissions"] == sorted(
        MEMBER_PERMISSIONS | {Permission.COMPANY_UPDATE}
    )


def test_changing_what_a_role_grants_changes_everyone_who_has_it(
    admin, company, member
):
    auditor = create(admin, "Auditor", {Permission.SESSION_READ}).json()
    assign(admin, member, role_named(company, "Auditor"))

    changed = client_for(admin).patch(
        f"{ROLES}/{auditor['id']}",
        {"permissions": [Permission.SESSION_READ, Permission.MEMBER_READ]},
        format="json",
    )

    assert changed.status_code == 200
    assert set(fresh(member).permissions) == {
        Permission.SESSION_READ,
        Permission.MEMBER_READ,
    }
    assert changed.json()["members"] == 1


def test_a_role_can_be_renamed(admin, company):
    auditor = create(admin, "Auditor", {Permission.SESSION_READ}).json()

    response = client_for(admin).patch(
        f"{ROLES}/{auditor['id']}", {"name": "Reviewer"}, format="json"
    )

    assert response.json()["name"] == "Reviewer"
    assert response.json()["permissions"] == [Permission.SESSION_READ]


def test_a_role_nobody_has_can_be_deleted(admin, company):
    auditor = create(admin, "Auditor", {Permission.SESSION_READ}).json()

    response = client_for(admin).delete(f"{ROLES}/{auditor['id']}")

    assert response.status_code == 204
    assert not Role.objects.filter(org=company, name="Auditor").exists()


def test_a_role_people_have_cannot_be_deleted(admin, company, member):
    auditor = create(admin, "Auditor", {Permission.SESSION_READ}).json()
    assign(admin, member, role_named(company, "Auditor"))

    response = client_for(admin).delete(f"{ROLES}/{auditor['id']}")

    assert response.status_code == 409
    assert "1 person has this role" in response.json()["detail"]


def test_unknown_permissions_and_blank_names_are_rejected(admin):
    assert create(admin, "Bad", {"launch_missiles"}).status_code == 400
    assert create(admin, "  ", {Permission.SESSION_READ}).status_code == 400


def test_a_role_name_is_taken_whatever_its_case(admin):
    create(admin, "Auditor", {Permission.SESSION_READ})

    response = create(admin, "auditor", {Permission.SESSION_READ})

    assert response.status_code == 409


# --- the built-in roles stay as they are -------------------------------------------


@pytest.mark.parametrize("name", ["Host", "Member"])
def test_the_built_in_roles_cannot_be_changed_or_deleted(admin, company, name):
    role = role_named(company, name)

    renamed = client_for(admin).patch(
        f"{ROLES}/{role.id}", {"name": "Boss"}, format="json"
    )
    narrowed = client_for(admin).patch(
        f"{ROLES}/{role.id}", {"permissions": []}, format="json"
    )
    deleted = client_for(admin).delete(f"{ROLES}/{role.id}")

    assert (renamed.status_code, narrowed.status_code, deleted.status_code) == (
        409,
        409,
        409,
    )
    assert role.grants.count() == len(
        ALL_PERMISSIONS if name == "Host" else MEMBER_PERMISSIONS
    )


# --- who may give what -------------------------------------------------------------


@pytest.fixture
def manager(company):
    """Can change roles, but isn't a host."""
    return make_user(company, "Mona", MEMBER_PERMISSIONS | {Permission.MEMBER_UPDATE})


def test_people_cannot_make_a_role_with_permissions_they_do_not_have(manager):
    response = create(manager, "Reviewer", {Permission.REVIEW_CREATE})

    assert response.status_code == 403
    assert Permission.REVIEW_CREATE in response.json()["detail"]


def test_people_cannot_give_a_role_that_grants_what_they_do_not_have(
    admin, manager, company, member
):
    create(admin, "Reviewer", {Permission.REVIEW_CREATE})

    response = assign(manager, member, role_named(company, "Reviewer"))

    assert response.status_code == 403
    assert Permission.REVIEW_CREATE in response.json()["detail"]
    assert not fresh(member).has_perm(Permission.REVIEW_CREATE)


def test_people_cannot_take_away_what_they_do_not_have(manager, company, host):
    response = assign(manager, host, role_named(company, "Member"))

    assert response.status_code == 403
    assert fresh(host).has_perm(Permission.SESSION_CREATE)


def test_people_cannot_widen_a_role_beyond_their_own_permissions(admin, manager):
    reviewer = create(admin, "Reviewer", {Permission.SESSION_READ}).json()

    response = client_for(manager).patch(
        f"{ROLES}/{reviewer['id']}",
        {"permissions": [Permission.SESSION_READ, Permission.REVIEW_CREATE]},
        format="json",
    )

    assert response.status_code == 403


def test_someone_in_the_company_always_keeps_member_update(admin, company):
    only_member = role_named(company, "Member")

    response = assign(admin, admin, only_member)

    assert response.status_code == 409
    assert fresh(admin).has_perm(Permission.MEMBER_UPDATE)
    assert [r.name for r in admin.roles.all()] == ["Host"]


def test_a_role_cannot_lose_member_update_if_it_is_the_last_to_have_it(admin, company):
    managers = create(admin, "Managers", {Permission.MEMBER_UPDATE}).json()
    assign(admin, admin, role_named(company, "Managers"))

    response = client_for(admin).patch(
        f"{ROLES}/{managers['id']}", {"permissions": []}, format="json"
    )

    assert response.status_code == 409
    assert fresh(admin).has_perm(Permission.MEMBER_UPDATE)


def test_changing_roles_can_be_handed_over(admin, company, member):
    host = role_named(company, "Host")
    assert assign(admin, member, host).status_code == 200

    response = assign(admin, admin, role_named(company, "Member"))

    assert response.status_code == 200
    assert response.json()["permissions"] == sorted(MEMBER_PERMISSIONS)
    assert fresh(member).has_perm(Permission.MEMBER_UPDATE)


def test_a_change_from_someone_who_just_lost_member_update_is_refused(company, member):
    manager = make_user(company, "Mona", ALL_PERMISSIONS)
    keeper = make_user(company, "Kai", ALL_PERMISSIONS)
    rbac.set_roles(keeper, manager, [role_named(company, "Member").id])

    with pytest.raises(PermissionDenied):
        rbac.set_roles(manager, member, [role_named(company, "Host").id])


# --- the company's own roles -------------------------------------------------------


def test_another_companys_role_cannot_be_given(admin, member, other_company):
    theirs = make_role(other_company, "Auditor", {Permission.SESSION_READ})

    response = assign(admin, member, theirs)

    assert response.status_code == 400
    assert fresh(member).permissions == sorted(MEMBER_PERMISSIONS)


def test_another_companys_person_and_role_are_out_of_reach(
    admin, company, other_company
):
    outsider = make_user(other_company, "Otto")
    theirs = make_role(other_company, "Auditor", {Permission.SESSION_READ})

    assert assign(admin, outsider, role_named(company, "Member")).status_code == 404
    patched = client_for(admin).patch(
        f"{ROLES}/{theirs.id}", {"name": "X"}, format="json"
    )
    assert patched.status_code == 404
    assert client_for(admin).delete(f"{ROLES}/{theirs.id}").status_code == 404


def test_two_companies_can_each_have_an_auditor(admin, other_company):
    make_role(other_company, "Auditor", {Permission.SESSION_READ})

    assert create(admin, "Auditor", {Permission.SESSION_READ}).status_code == 201


def test_a_person_with_no_roles_may_do_nothing(company):
    nobody = make_user(company, "Nia", set())

    assert nobody.permissions == []
    assert client_for(nobody).get("/api/lunches").status_code == 403
    assert client_for(nobody).get("/api/me").status_code == 200


def test_role_changes_reach_firebase_for_the_board_rules(
    admin, company, member, settings, monkeypatch, django_capture_on_commit_callbacks
):
    settings.FIRESTORE_ENABLED = True
    sent = {}
    monkeypatch.setattr(rbac, "_set_claims", lambda uid, claims: sent.update(claims))
    auditor = create(admin, "Auditor", {Permission.SESSION_READ}).json()

    with django_capture_on_commit_callbacks(execute=True):
        assign(admin, member, role_named(company, "Auditor"))
        client_for(admin).patch(
            f"{ROLES}/{auditor['id']}",
            {"permissions": [Permission.MEMBER_READ]},
            format="json",
        )

    assert sent["org_id"] == str(member.org_id)
    assert sent["permissions"] == [Permission.MEMBER_READ]


def test_the_members_list_shows_each_persons_roles(admin, company, member):
    create(admin, "Auditor", {Permission.SESSION_READ})
    assign(admin, member, role_named(company, "Auditor"))

    people = {
        p["name"]: p for p in client_for(admin).get("/api/company/members").json()
    }

    assert [r["name"] for r in people["Meera"]["roles"]] == ["Auditor"]
    assert people["Meera"]["permissions"] == [Permission.SESSION_READ]
    assert [r["name"] for r in people["Asha"]["roles"]] == ["Host"]


def test_started_lunches_still_follow_roles(company, member, admin):
    assign(admin, member, role_named(company, "Host"))
    cutoff = timezone.now() + timedelta(hours=1)

    started = client_for(fresh(member)).post(
        "/api/lunches",
        {"cutoff_at": cutoff.isoformat(), "address": {"address": "Office"}},
        format="json",
    )

    assert started.status_code == 201
