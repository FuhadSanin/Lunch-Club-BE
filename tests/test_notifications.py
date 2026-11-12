"""Push notifications: devices, the topics they hear, and what is sent when."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from firebase_admin import messaging

from apps.accounts.models import DeviceToken
from apps.core import firebase
from apps.lunches import services
from apps.lunches.models import OrderSession
from tests.conftest import client_for
from tests.test_parties import join, person, start


@pytest.fixture
def push(settings, monkeypatch):
    """Push on, with Firebase's calls recorded instead of sent."""
    settings.PUSH_ENABLED = True
    calls = SimpleNamespace(sent=[], subscribed=[], unsubscribed=[])
    monkeypatch.setattr(firebase, "app", lambda: "app")
    monkeypatch.setattr(messaging, "send", lambda m, app: calls.sent.append(m))
    monkeypatch.setattr(
        messaging, "send_each_for_multicast", lambda m, app: calls.sent.append(m)
    )
    monkeypatch.setattr(
        messaging,
        "subscribe_to_topic",
        lambda tokens, topic, app: calls.subscribed.append((tokens, topic)),
    )
    monkeypatch.setattr(
        messaging,
        "unsubscribe_from_topic",
        lambda tokens, topic, app: calls.unsubscribed.append((tokens, topic)),
    )
    return calls


def register(user, device="device-1"):
    return client_for(user).post("/api/me/push-token", {"token": device}, format="json")


def test_a_company_member_hears_the_company(push, member):
    assert register(member).status_code == 204

    assert DeviceToken.objects.get(token="device-1").user == member
    assert push.subscribed == [(["device-1"], f"org-{member.org_id}")]


def test_registering_the_same_device_twice_keeps_one_row(push, member):
    register(member)
    register(member)

    assert DeviceToken.objects.count() == 1


def test_someone_with_no_company_can_turn_notifications_on(push, db):
    guest = person("solo")

    assert register(guest).status_code == 204
    assert push.subscribed == []  # no company and no party yet


def test_turning_them_off_forgets_the_device_and_leaves_the_topic(push, member):
    register(member)

    response = client_for(member).delete(
        "/api/me/push-token", {"token": "device-1"}, format="json"
    )

    assert response.status_code == 204
    assert not DeviceToken.objects.exists()
    assert push.unsubscribed == [(["device-1"], f"org-{member.org_id}")]


def test_someone_cannot_remove_another_persons_device(push, member, host):
    register(member)

    client_for(host).delete("/api/me/push-token", {"token": "device-1"}, format="json")

    assert DeviceToken.objects.filter(token="device-1").exists()


def test_starting_a_lunch_sends_no_push(push, host, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        start(host)

    assert push.sent == []


def _lunch():
    return OrderSession.objects.get()


def test_a_party_host_hears_their_own_lunch(
    push, db, django_capture_on_commit_callbacks
):
    host = person("hana")
    register(host, "host-device")

    with django_capture_on_commit_callbacks(execute=True):
        start(host)

    topic = f"lunch-{_lunch().id}"
    assert (["host-device"], topic) in push.subscribed
    assert push.sent == []  # only someone joining is pushed


def test_the_host_gets_a_push_on_their_own_devices_when_someone_joins(
    push, db, django_capture_on_commit_callbacks
):
    host, guest = person("hana"), person("gus")
    register(host, "host-device")
    with django_capture_on_commit_callbacks(execute=True):
        start(host)
    register(guest, "guest-device")

    with django_capture_on_commit_callbacks(execute=True):
        join(guest, _lunch().id)

    assert len(push.sent) == 1
    message = push.sent[0]
    assert message.tokens == ["host-device"]
    assert message.notification.title == "Gus joined your party"
    assert message.webpush.fcm_options.link.endswith(f"/lunch/{_lunch().id}")


def test_a_guest_who_joins_hears_the_lunch(
    push, db, django_capture_on_commit_callbacks
):
    host, guest = person("hana"), person("gus")
    with django_capture_on_commit_callbacks(execute=True):
        start(host)
    register(guest, "guest-device")  # no lunch yet: nothing to subscribe to
    topic = f"lunch-{_lunch().id}"
    assert (["guest-device"], topic) not in push.subscribed

    with django_capture_on_commit_callbacks(execute=True):
        join(guest, _lunch().id)

    assert (["guest-device"], topic) in push.subscribed


def test_a_device_registered_while_in_a_party_hears_it(
    push, db, django_capture_on_commit_callbacks
):
    host = person("hana")
    with django_capture_on_commit_callbacks(execute=True):
        start(host)

    register(host, "late-device")

    assert (["late-device"], f"lunch-{_lunch().id}") in push.subscribed


def test_later_updates_to_a_party_go_to_its_lunch_topic(
    push, db, django_capture_on_commit_callbacks
):
    host = person("hana")
    start(host)
    lunch = OrderSession.objects.select_related("org").get()
    push.sent.clear()

    with django_capture_on_commit_callbacks(execute=True):
        services.tell_participants(lunch, "Lunch is ordered", "Swiggy is on it.")

    assert [m.topic for m in push.sent] == [f"lunch-{lunch.id}"]


def test_nothing_is_sent_until_the_change_commits(
    push, host, django_capture_on_commit_callbacks
):
    # Sending is best effort and only after the change commits: nothing is sent
    # until then.
    cutoff = timezone.now() + timedelta(hours=1)
    with django_capture_on_commit_callbacks(execute=False):
        client_for(host).post(
            "/api/lunches",
            {"cutoff_at": cutoff.isoformat(), "delivery_address": "Home"},
            format="json",
        )

    assert push.sent == []


def test_firebase_uses_a_service_account_key_when_one_is_given(settings, monkeypatch):
    seen = {}
    key = {"type": "service_account", "project_id": "p"}
    settings.FIREBASE_CREDENTIALS_JSON = json.dumps(key)
    firebase.app.cache_clear()
    monkeypatch.setattr(
        firebase.credentials, "Certificate", lambda info: ("cert", info)
    )
    monkeypatch.setattr(
        firebase.firebase_admin,
        "initialize_app",
        lambda credential=None, options=None: (
            seen.update(credential=credential) or "app"
        ),
    )

    assert firebase.app() == "app"
    assert seen["credential"] == ("cert", key)
    firebase.app.cache_clear()


# ---- the bell: what people were told, kept to read later ----


def bell(user):
    return client_for(user).get("/api/notifications").json()


def test_starting_a_lunch_notifies_nobody(host, member, admin):
    start(host)

    for person_ in (host, member, admin):
        assert bell(person_) == {"unread": 0, "items": []}


def test_the_host_is_told_when_someone_joins_their_party(db):
    host, guest = person("hana"), person("gus")
    start(host)

    join(guest, _lunch().id)

    got = bell(host)
    assert got["unread"] == 1
    item = got["items"][0]
    assert item["title"] == "Gus joined your party"
    assert item["body"] == "Your lunch has 2 people now."
    assert item["link"].endswith(f"/#/lunch/{_lunch().id}")
    assert bell(guest) == {"unread": 0, "items": []}


def test_joining_again_does_not_tell_the_host_twice(db):
    host, guest = person("hana"), person("gus")
    start(host)

    join(guest, _lunch().id)
    join(guest, _lunch().id)

    assert bell(host)["unread"] == 1


def test_a_company_does_not_hear_about_another_companys_lunch(host, other_company):
    from tests.conftest import make_user

    outsider = make_user(other_company, "Olga")

    start(host)

    assert bell(outsider) == {"unread": 0, "items": []}


def test_tell_participants_reaches_everyone_in_a_party(db):
    host, guest, stranger = person("hana"), person("gus"), person("sam")
    start(host)
    join(guest, _lunch().id)
    lunch = OrderSession.objects.select_related("org").get()

    services.tell_participants(lunch, "Lunch is ordered", "Swiggy is on it.")

    assert [i["title"] for i in bell(guest)["items"]] == ["Lunch is ordered"]
    assert [i["title"] for i in bell(host)["items"]] == [
        "Lunch is ordered",
        "Gus joined your party",
    ]
    assert bell(stranger) == {"unread": 0, "items": []}


def test_marking_some_read_and_then_all(db):
    host = person("hana")
    start(host)
    join(person("gus"), _lunch().id)
    join(person("ivy"), _lunch().id)
    first, second = bell(host)["items"][::-1]
    member = host

    client = client_for(member)
    assert (
        client.post(
            "/api/notifications/read", {"ids": [first["id"]]}, format="json"
        ).status_code
        == 204
    )
    assert bell(member)["unread"] == 1
    assert [i["read"] for i in bell(member)["items"]] == [False, True]

    client.post("/api/notifications/read", {}, format="json")
    assert bell(member)["unread"] == 0
    assert second["title"] == "Ivy joined your party"


def test_someone_cannot_mark_another_persons_notifications_read(db):
    host, guest = person("hana"), person("gus")
    start(host)
    join(guest, _lunch().id)
    mine = bell(host)["items"][0]["id"]

    client_for(guest).post("/api/notifications/read", {"ids": [mine]}, format="json")

    assert bell(host)["unread"] == 1


def test_signed_out_people_get_no_bell(db, client):
    assert client.get("/api/notifications").status_code == 401


# --- the host hears who has finished picking ----------------------------------


DISH = {
    "restaurant_name": "Meghana Foods",
    "restaurant_id": "1234",
    "dish_name": "Chicken Biryani",
    "quantity": 1,
    "price_paise": 32000,
}


def complete_order(user, lunch_id):
    client = client_for(user)
    client.post(f"/api/lunches/{lunch_id}/items", DISH, format="json")
    return client.put(f"/api/lunches/{lunch_id}/complete")


def titles(user):
    return [i["title"] for i in bell(user)["items"]]


def test_the_host_is_told_when_someone_completes_their_order(db):
    host, gus, ivy = person("hana"), person("gus"), person("ivy")
    start(host)
    join(gus, _lunch().id)
    join(ivy, _lunch().id)

    assert complete_order(gus, _lunch().id).status_code == 200

    item = bell(host)["items"][0]
    assert item["title"] == "Gus completed their order"
    assert item["body"] == "1 of 3 have picked."
    assert titles(gus) == []


def test_the_host_is_told_once_when_everyone_has_picked(db):
    host, gus = person("hana"), person("gus")
    start(host)
    join(gus, _lunch().id)

    complete_order(gus, _lunch().id)
    complete_order(host, _lunch().id)

    assert titles(host)[:2] == ["Everyone has picked", "Gus completed their order"]
    assert titles(host).count("Everyone has picked") == 1
    assert "Hana completed their order" not in titles(host)  # not told about yourself


def test_skipping_counts_towards_everyone_but_is_not_announced(db):
    host, gus = person("hana"), person("gus")
    start(host)
    join(gus, _lunch().id)
    complete_order(host, _lunch().id)

    client_for(gus).put(f"/api/lunches/{_lunch().id}/skip")

    assert titles(host)[0] == "Everyone has picked"
    assert not any("Gus completed" in t for t in titles(host))


def test_completing_again_tells_the_host_nothing_new(db):
    host, gus = person("hana"), person("gus")
    start(host)
    join(gus, _lunch().id)
    complete_order(gus, _lunch().id)
    before = len(titles(host))

    client_for(gus).put(f"/api/lunches/{_lunch().id}/complete")

    assert len(titles(host)) == before
