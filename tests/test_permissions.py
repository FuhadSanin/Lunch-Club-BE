import pytest
from django.urls import URLResolver, get_resolver

from apps.accounts.models import ALL_PERMISSIONS, MEMBER_PERMISSIONS, Permission
from apps.accounts.permissions import (
    HasRequiredPermission,
    MayStartOwnLunch,
    MayTakePart,
)
from apps.catalog.views import CanBrowse
from apps.lunches.models import Order, OrderItem
from tests.conftest import client_for, make_user

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# Every endpoint behind a permission, and the one it needs.
ENDPOINTS = [
    ("get", "/api/lunches", Permission.SESSION_READ),
    ("post", "/api/lunches", Permission.SESSION_CREATE),
    ("get", "/api/lunches/{lunch}", Permission.SESSION_READ),
    ("post", "/api/lunches/{lunch}/lock", Permission.SESSION_UPDATE),
    ("put", "/api/lunches/{lunch}/ticks", Permission.SESSION_UPDATE),
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
    ("get", "/api/places?q=infopark", Permission.RESTAURANT_READ),
    ("get", "/api/places/W731817480", Permission.RESTAURANT_READ),
    ("get", "/api/company/members", Permission.MEMBER_READ),
    ("patch", "/api/company/settings", Permission.COMPANY_UPDATE),
    ("post", "/api/company/addresses", Permission.COMPANY_UPDATE),
    ("delete", "/api/company/addresses/{member}", Permission.COMPANY_UPDATE),
    ("get", "/api/company/invites", Permission.MEMBER_READ),
    ("post", "/api/company/invites", Permission.MEMBER_CREATE),
    ("delete", "/api/company/invites/{member}", Permission.MEMBER_DELETE),
    ("get", "/api/company/roles", Permission.MEMBER_READ),
    ("post", "/api/company/roles", Permission.MEMBER_UPDATE),
    ("patch", "/api/company/roles/{member}", Permission.MEMBER_UPDATE),
    ("delete", "/api/company/roles/{member}", Permission.MEMBER_UPDATE),
    ("put", "/api/company/members/{member}/roles", Permission.MEMBER_UPDATE),
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
        classes = getattr(view, "permission_classes", [])
        checks = (HasRequiredPermission, MayTakePart, MayStartOwnLunch, CanBrowse)
        if view and any(check in classes for check in checks):
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
    order = Order.objects.create(session=lunch, restaurant_count=1, total_paise=32000)
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


def test_being_refused_says_what_you_cannot_do_and_who_can_fix_it(member):
    response = client_for(member).post("/api/lunches", {}, format="json")

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Your role doesn't let you start a food party. Ask a host to change your role. "
        "(It needs session_create.)"
    )


def test_every_permission_has_words_for_what_it_allows():
    from apps.accounts.messages import not_allowed

    for permission in Permission:
        assert "do this" not in not_allowed(permission), permission


def test_something_that_is_not_there_is_said_plainly(admin):
    missing = "00000000-0000-0000-0000-000000000000"

    response = client_for(admin).get(f"/api/lunches/{missing}")

    assert response.status_code == 404
    assert response.json()["detail"].startswith("We couldn't find that.")
    assert "matches the given query" not in response.json()["detail"]
