from datetime import timedelta

import pytest
from django.urls import URLResolver, get_resolver
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts import services
from apps.accounts.models import ALL_PERMISSIONS, MEMBER_PERMISSIONS, Permission
from apps.accounts.permissions import HasRequiredPermission
from apps.lunches.models import OrderItem, SwiggyOrder
from tests.conftest import client_for, make_user

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# Every endpoint behind a permission, and the one it needs.
ENDPOINTS = [
    ("get", "/api/lunches", Permission.SESSION_READ),
    ("post", "/api/lunches", Permission.SESSION_CREATE),
    ("get", "/api/lunches/{lunch}", Permission.SESSION_READ),
    ("post", "/api/lunches/{lunch}/lock", Permission.SESSION_UPDATE),
    ("post", "/api/lunches/{lunch}/cancel", Permission.SESSION_DELETE),
    ("post", "/api/lunches/{lunch}/items", Permission.PICK_CREATE),
    ("patch", "/api/lunches/{lunch}/items/{item}", Permission.PICK_UPDATE),
    ("delete", "/api/lunches/{lunch}/items/{item}", Permission.PICK_DELETE),
    ("put", "/api/lunches/{lunch}/skip", Permission.PICK_UPDATE),
    ("delete", "/api/lunches/{lunch}/skip", Permission.PICK_UPDATE),
    ("post", "/api/lunches/{lunch}/confirm", Permission.REVIEW_CREATE),
    ("post", "/api/orders/{order}/placed", Permission.ORDER_UPDATE),
    ("post", "/api/orders/{order}/delivered", Permission.ORDER_UPDATE),
    ("get", "/api/restaurants?lat=10&lng=76", Permission.RESTAURANT_READ),
    ("get", "/api/restaurants/1/menu?lat=10&lng=76", Permission.RESTAURANT_READ),
    ("get", "/api/dishes?lat=10&lng=76&q=dosa", Permission.RESTAURANT_READ),
    ("get", "/api/company/members", Permission.MEMBER_READ),
    ("patch", "/api/company/settings", Permission.COMPANY_UPDATE),
    ("post", "/api/company/addresses", Permission.COMPANY_UPDATE),
    ("delete", "/api/company/addresses/{member}", Permission.COMPANY_UPDATE),
    ("get", "/api/company/invites", Permission.MEMBER_READ),
    ("post", "/api/company/invites", Permission.MEMBER_CREATE),
    ("delete", "/api/company/invites/{member}", Permission.MEMBER_DELETE),
    ("put", "/api/company/members/{member}/permissions", Permission.MEMBER_UPDATE),
]


def guarded_views():
    """Every view that checks permissions, from the project's URLs."""

    def walk(patterns):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns)
            else:
                yield pattern

    for pattern in walk(get_resolver().url_patterns):
        view = getattr(pattern.callback, "view_class", None)
        if view and HasRequiredPermission in getattr(view, "permission_classes", []):
            yield view


def test_every_company_endpoint_names_the_permission_each_method_needs():
    views = list(guarded_views())

    assert len(views) >= 15
    for view in views:
        for method in HTTP_METHODS:
            if hasattr(view, method):
                assert method.upper() in view.required_permissions, (view, method)


def test_every_permission_guards_something():
    used = {
        permission
        for view in guarded_views()
        for permission in view.required_permissions.values()
    }

    assert used - {None} == ALL_PERMISSIONS


@pytest.fixture
def ids(lunch, member):
    item = OrderItem.objects.create(
        session=lunch,
        user=member,
        restaurant_name="Meghana Foods",
        dish_name="Chicken Biryani",
        quantity=1,
        price_paise=32000,
    )
    order = SwiggyOrder.objects.create(
        session=lunch, restaurant_count=1, total_paise=32000
    )
    return {"lunch": lunch.id, "item": item.id, "order": order.id, "member": member.id}


@pytest.mark.parametrize(
    ("method", "path", "permission"),
    ENDPOINTS,
    ids=[f"{method} {path}" for method, path, _ in ENDPOINTS],
)
def test_each_action_needs_its_permission(company, ids, method, path, permission):
    almost_everything = make_user(company, "Pia", ALL_PERMISSIONS - {permission})

    response = getattr(client_for(almost_everything), method)(
        path.format(**ids), {}, format="json"
    )

    assert response.status_code == 403
    assert permission in response.json()["detail"]


def set_permissions(by, person, permissions):
    return client_for(by).put(
        f"/api/company/members/{person.id}/permissions",
        {"permissions": sorted(permissions)},
        format="json",
    )


def test_granting_a_permission_lets_the_person_do_it(admin, member):
    response = set_permissions(
        admin, member, MEMBER_PERMISSIONS | {Permission.SESSION_CREATE}
    )

    assert response.status_code == 200
    assert Permission.SESSION_CREATE in response.json()["permissions"]
    member.refresh_from_db()  # a real request loads the person afresh
    cutoff = timezone.now() + timedelta(hours=1)
    started = client_for(member).post(
        "/api/lunches",
        {"cutoff_at": cutoff.isoformat(), "delivery_address": "Office"},
        format="json",
    )
    assert started.status_code == 201


def test_unknown_permissions_are_rejected(admin, member):
    response = set_permissions(admin, member, {"session_read", "launch_missiles"})

    assert response.status_code == 400


def test_people_cannot_grant_permissions_they_do_not_have(company, member):
    manager = make_user(
        company, "Mona", MEMBER_PERMISSIONS | {Permission.MEMBER_UPDATE}
    )

    response = set_permissions(
        manager, member, MEMBER_PERMISSIONS | {Permission.REVIEW_CREATE}
    )

    assert response.status_code == 403
    assert Permission.REVIEW_CREATE in response.json()["detail"]
    member.refresh_from_db()
    assert Permission.REVIEW_CREATE not in member.permissions


def test_people_cannot_remove_permissions_they_do_not_have(company, host):
    manager = make_user(
        company, "Mona", MEMBER_PERMISSIONS | {Permission.MEMBER_UPDATE}
    )

    response = set_permissions(manager, host, MEMBER_PERMISSIONS)

    assert response.status_code == 403


def test_the_last_person_who_can_change_permissions_keeps_that(admin):
    response = set_permissions(
        admin, admin, ALL_PERMISSIONS - {Permission.MEMBER_UPDATE}
    )

    assert response.status_code == 409
    admin.refresh_from_db()
    assert Permission.MEMBER_UPDATE in admin.permissions


def test_changing_permissions_can_be_handed_over(admin, member):
    assert set_permissions(admin, member, ALL_PERMISSIONS).status_code == 200

    response = set_permissions(admin, admin, MEMBER_PERMISSIONS)

    assert response.status_code == 200
    assert response.json()["permissions"] == sorted(MEMBER_PERMISSIONS)


def test_another_companys_person_is_out_of_reach(admin, other_company):
    outsider = make_user(other_company, "Otto")

    assert set_permissions(admin, outsider, ALL_PERMISSIONS).status_code == 404


def test_people_see_their_own_permissions(member):
    me = client_for(member).get("/api/me").json()

    assert me["permissions"] == sorted(MEMBER_PERMISSIONS)


def test_the_permission_list_says_what_each_one_allows(member):
    response = client_for(member).get("/api/permissions")

    assert response.status_code == 200
    listed = response.json()
    assert [p["code"] for p in listed] == Permission.values
    assert all(p["description"] for p in listed)


def test_a_method_the_view_does_not_name_is_refused(admin):
    assert client_for(admin).options("/api/lunches").status_code == 403


def test_a_change_from_someone_who_just_lost_member_update_is_refused(company, member):
    manager = make_user(company, "Mona", ALL_PERMISSIONS)
    keeper = make_user(company, "Kai", ALL_PERMISSIONS)
    services.set_permissions(keeper, manager, MEMBER_PERMISSIONS)

    with pytest.raises(PermissionDenied):
        services.set_permissions(manager, member, ALL_PERMISSIONS)


def test_permission_changes_reach_firebase_for_the_board_rules(
    admin, member, settings, monkeypatch, django_capture_on_commit_callbacks
):
    settings.FIRESTORE_ENABLED = True
    sent = {}
    monkeypatch.setattr(
        services, "_set_claims", lambda uid, claims: sent.update(claims)
    )

    with django_capture_on_commit_callbacks(execute=True):
        set_permissions(admin, member, MEMBER_PERMISSIONS - {Permission.SESSION_READ})

    assert sent["org_id"] == str(member.org_id)
    assert Permission.SESSION_READ not in sent["permissions"]
