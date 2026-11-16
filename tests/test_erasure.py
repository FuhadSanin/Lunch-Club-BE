"""Deleting an account: a person can ask us to forget them."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import ApiToken, DeviceToken, Notification, User
from apps.lunches.models import LocalMenu, OrderItem, SessionStatus
from tests.conftest import client_for


@pytest.fixture(autouse=True)
def no_firebase(monkeypatch):
    """Deleting the sign-in itself is tidying up; the tests do not reach Firebase."""
    monkeypatch.setattr("firebase_admin.auth.delete_user", lambda *a, **k: None)
    monkeypatch.setattr("apps.core.firebase.app", lambda: None)


def pick(lunch, user, name="Paneer roll"):
    return OrderItem.objects.create(
        session=lunch,
        user=user,
        restaurant_id="r1",
        restaurant_name="Lunchbox",
        dish_name=name,
        price_paise=18000,
        quantity=1,
    )


def test_the_person_is_erased_but_not_the_record_others_rely_on(member, lunch):
    services.issue_api_token(member, "Claude", timedelta(days=30))
    DeviceToken.objects.create(user=member, token="t" * 20)
    Notification.objects.create(user=member, title="Hi", body="there")
    LocalMenu.objects.create(owner=member, name="Mess", items=[])
    lunch.status = SessionStatus.DELIVERED
    lunch.save()
    pick(lunch, member)

    response = client_for(member).delete("/api/me")

    assert response.status_code == 204
    member.refresh_from_db()
    assert member.name == "Former member"
    assert "@deleted.invalid" in member.email
    assert member.org_id is None
    assert not ApiToken.objects.filter(user=member).exists()
    assert not DeviceToken.objects.filter(user=member).exists()
    assert not Notification.objects.filter(user=member).exists()
    assert not LocalMenu.objects.filter(owner=member).exists()
    assert (
        OrderItem.objects.filter(user=member).count() == 1
    )  # the finished lunch keeps its record


def test_picks_in_a_party_still_taking_picks_leave_with_the_person(member, lunch):
    pick(lunch, member)

    assert client_for(member).delete("/api/me").status_code == 204

    assert not OrderItem.objects.filter(user=member).exists()


def test_a_host_with_a_running_food_party_must_finish_it_first(host, lunch):
    response = client_for(host).delete("/api/me")

    assert response.status_code == 409
    assert "hosting" in response.json()["detail"]
    assert User.objects.get(id=host.id).name == host.name


def test_food_that_is_already_being_ordered_blocks_it(member, lunch):
    pick(lunch, member)
    lunch.status = SessionStatus.CONFIRMED
    lunch.cutoff_at = timezone.now() - timedelta(minutes=5)
    lunch.save()

    response = client_for(member).delete("/api/me")

    assert response.status_code == 409
    assert User.objects.get(id=member.id).name == member.name


def test_someone_signed_out_cannot_delete_anything(db):
    from rest_framework.test import APIClient

    assert APIClient().delete("/api/me").status_code in (401, 403)
