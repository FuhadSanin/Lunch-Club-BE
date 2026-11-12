import logging
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.core import jobs, notify
from apps.core.errors import RetryLater
from apps.lunches import board, services
from apps.lunches.jobs import lock_lunch
from apps.lunches.models import (
    OrderItem,
    OrderSession,
    SessionStatus,
    SwiggyOrder,
)
from tests.conftest import client_for, make_user

DISH = {
    "restaurant_name": "Dosa Hut",
    "restaurant_id": "111",
    "dish_name": "Masala Dosa",
    "quantity": 2,
    "price_paise": 12000,
}


def pick(client, lunch, **overrides):
    return client.post(
        f"/api/lunches/{lunch.id}/items", {**DISH, **overrides}, format="json"
    )


def close_picks(lunch):
    past = timezone.now() - timedelta(minutes=1)
    OrderSession.objects.filter(id=lunch.id).update(cutoff_at=past)
    lunch.refresh_from_db()


def in_hours(hours):
    return (timezone.now() + timedelta(hours=hours)).isoformat()


def test_host_starts_a_lunch(host, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(host).post(
            "/api/lunches",
            {"cutoff_at": in_hours(2), "delivery_address": "Tower A"},
            format="json",
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["host"]["name"] == "Hari"
    assert body["items"] == []
    listed = client_for(host).get("/api/lunches").json()
    assert [row["id"] for row in listed] == [body["id"]]


def test_starting_a_lunch_needs_session_create(member):
    response = client_for(member).post(
        "/api/lunches",
        {"cutoff_at": in_hours(2), "delivery_address": "Tower A"},
        format="json",
    )

    assert response.status_code == 403


@pytest.mark.parametrize("hours", [-1, 30])
def test_cutoff_must_be_within_the_next_day(host, hours):
    response = client_for(host).post(
        "/api/lunches",
        {"cutoff_at": in_hours(hours), "delivery_address": "Tower A"},
        format="json",
    )

    assert response.status_code == 400
    assert "cutoff_at" in response.json()


def test_member_picks_dishes_and_sees_them_on_the_board(lunch, member):
    client = client_for(member)

    response = pick(client, lunch)
    board_json = client.get(f"/api/lunches/{lunch.id}").json()

    assert response.status_code == 201
    assert board_json["items"][0]["dish_name"] == "Masala Dosa"
    assert board_json["items"][0]["user"]["name"] == "Meera"
    assert board_json["total_paise"] == 24000


def test_another_company_cannot_open_the_lunch(lunch, other_company):
    outsider = make_user(other_company, "Olga")
    client = client_for(outsider)

    assert client.get(f"/api/lunches/{lunch.id}").status_code == 404
    assert pick(client, lunch).status_code == 404


def test_people_change_only_their_own_picks(lunch, member, host):
    client = client_for(member)
    item_id = pick(client, lunch).json()["id"]
    url = f"/api/lunches/{lunch.id}/items/{item_id}"

    mine = client.patch(url, {"quantity": 3}, format="json")
    theirs = client_for(host).patch(url, {"quantity": 9}, format="json")
    stolen = client_for(host).delete(url)
    dropped = client.delete(url)

    assert mine.json()["quantity"] == 3
    assert theirs.status_code == 404
    assert stolen.status_code == 404
    assert dropped.status_code == 204
    assert not OrderItem.objects.exists()


def test_skipping_drops_picks_and_is_final(lunch, member):
    client = client_for(member)
    pick(client, lunch)

    skipped = client.put(f"/api/lunches/{lunch.id}/skip").json()
    again = pick(client, lunch)

    assert skipped["items"] == []
    assert [p["name"] for p in skipped["skipped"]] == ["Meera"]
    assert again.status_code == 409


def test_a_person_can_rejoin_after_being_reopened(lunch, member, host):
    client = client_for(member)
    client.put(f"/api/lunches/{lunch.id}/skip")
    client_for(host).delete(f"/api/lunches/{lunch.id}/complete/{member.id}")

    rejoined = client.delete(f"/api/lunches/{lunch.id}/skip")

    assert rejoined.status_code == 200
    assert rejoined.json()["skipped"] == []
    assert pick(client, lunch).status_code == 201


def test_completing_an_order_makes_it_final(lunch, member):
    client = client_for(member)
    pick(client, lunch)

    completed = client.put(f"/api/lunches/{lunch.id}/complete")
    again = client.put(f"/api/lunches/{lunch.id}/complete")
    more = pick(client, lunch)

    assert [p["name"] for p in completed.json()["completed"]] == ["Meera"]
    assert again.status_code == 200
    assert more.status_code == 409


def test_whole_cart_in_one_request(lunch, member):
    client = client_for(member)
    pick(client, lunch, dish_name="Old")

    response = client.put(
        f"/api/lunches/{lunch.id}/picks",
        {"items": [DISH, {**DISH, "dish_name": "Vada"}], "complete": True},
        format="json",
    )

    body = response.json()
    assert sorted(i["dish_name"] for i in body["items"]) == ["Masala Dosa", "Vada"]
    assert [p["name"] for p in body["completed"]] == ["Meera"]


def test_whole_cart_without_completing_stays_editable(lunch, member):
    client = client_for(member)

    client.put(f"/api/lunches/{lunch.id}/picks", {"items": [DISH]}, format="json")
    body = client.put(
        f"/api/lunches/{lunch.id}/picks", {"items": [DISH]}, format="json"
    ).json()

    assert body["completed"] == []
    assert len(body["items"]) == 1


def test_completing_an_empty_cart_is_refused(lunch, member):
    response = client_for(member).put(
        f"/api/lunches/{lunch.id}/picks",
        {"items": [], "complete": True},
        format="json",
    )

    assert response.status_code == 409


def test_completing_needs_a_pick(lunch, member):
    assert (
        client_for(member).put(f"/api/lunches/{lunch.id}/complete").status_code == 409
    )


def test_host_reopens_a_completed_order(lunch, member, host):
    client = client_for(member)
    pick(client, lunch)
    client.put(f"/api/lunches/{lunch.id}/complete")

    response = client_for(host).delete(f"/api/lunches/{lunch.id}/complete/{member.id}")

    assert response.json()["completed"] == []
    assert pick(client, lunch).status_code == 201


def test_host_reopens_a_skipped_day(lunch, member, host):
    client_for(member).put(f"/api/lunches/{lunch.id}/skip")

    response = client_for(host).delete(f"/api/lunches/{lunch.id}/complete/{member.id}")

    assert response.json()["skipped"] == []


def test_host_marks_someone_as_skipping(lunch, member, host):
    pick(client_for(member), lunch)

    response = client_for(host).put(f"/api/lunches/{lunch.id}/skip/{member.id}")

    assert [p["name"] for p in response.json()["skipped"]] == ["Meera"]
    assert response.json()["items"] == []


def test_skipping_someone_else_needs_session_update(lunch, member):
    other = make_user(member.org, "Nia")

    response = client_for(member).put(f"/api/lunches/{lunch.id}/skip/{other.id}")

    assert response.status_code == 403


def test_reopening_needs_session_update(lunch, member):
    response = client_for(member).delete(
        f"/api/lunches/{lunch.id}/complete/{member.id}"
    )

    assert response.status_code == 403


def test_picks_are_closed_after_the_cutoff(lunch, member):
    close_picks(lunch)
    client = client_for(member)

    assert pick(client, lunch).status_code == 409
    assert client.put(f"/api/lunches/{lunch.id}/skip").status_code == 409


def test_lock_splits_restaurants_into_checkouts_of_at_most_five(
    lunch, member, host, settings
):
    settings.MAX_RESTAURANTS_PER_ORDER = 5
    client = client_for(member)
    for n in range(6):
        pick(client, lunch, restaurant_name=f"Place {n}", dish_name=f"Dish {n}")

    response = client_for(host).post(f"/api/lunches/{lunch.id}/lock")

    orders = response.json()["orders"]
    assert response.json()["status"] == "locked"
    assert sorted(o["restaurant_count"] for o in orders) == [1, 5]
    assert sum(o["total_paise"] for o in orders) == 6 * 24000
    assert all(len(o["carts"]) == o["restaurant_count"] for o in orders)
    assert SwiggyOrder.objects.count() == 2
    # Locking twice changes nothing.
    client_for(host).post(f"/api/lunches/{lunch.id}/lock")
    assert SwiggyOrder.objects.count() == 2


def test_lock_emails_the_host_to_confirm(
    lunch, member, host, django_capture_on_commit_callbacks
):
    pick(client_for(member), lunch)

    with django_capture_on_commit_callbacks(execute=True):
        client_for(host).post(f"/api/lunches/{lunch.id}/lock")

    assert [m.to for m in mail.outbox] == [[host.email]]
    assert "confirm" in mail.outbox[0].subject


def test_locking_early_needs_session_update(lunch, member):
    response = client_for(member).post(f"/api/lunches/{lunch.id}/lock")

    assert response.status_code == 403


def test_a_lunch_nobody_picked_for_is_cancelled_at_lock(lunch, host):
    response = client_for(host).post(f"/api/lunches/{lunch.id}/lock")

    assert response.json()["status"] == "cancelled"


def test_cutoff_job_waits_until_the_cutoff(lunch, member):
    pick(client_for(member), lunch)
    payload = {"org_id": str(lunch.org_id), "lunch_id": str(lunch.id)}

    with pytest.raises(RetryLater):
        lock_lunch(payload)
    close_picks(lunch)
    lock_lunch(payload)
    lock_lunch({**payload, "lunch_id": "00000000-0000-0000-0000-000000000000"})

    lunch.refresh_from_db()
    assert lunch.status == SessionStatus.LOCKED


def test_host_cancels_an_open_lunch(lunch, host, member):
    pick(client_for(member), lunch)
    client = client_for(host)

    response = client.post(f"/api/lunches/{lunch.id}/cancel")
    again = client.post(f"/api/lunches/{lunch.id}/cancel")

    assert response.json()["status"] == "cancelled"
    assert again.status_code in {200, 409}


def test_a_confirmed_lunch_cannot_be_cancelled(lunch, host):
    lunch.status = SessionStatus.CONFIRMED
    lunch.save()

    assert client_for(host).post(f"/api/lunches/{lunch.id}/cancel").status_code == 409


def test_lunch_records_read_clearly(lunch, member):
    item = OrderItem.objects.create(session=lunch, user=member, order=None, **DISH)
    order = SwiggyOrder.objects.create(
        session=lunch, restaurant_count=1, total_paise=100
    )

    assert str(lunch).startswith("Lunch closing")
    assert "open" in str(lunch)
    assert str(item) == "2 × Masala Dosa from Dosa Hut"
    assert item.total_paise == 24000
    assert "pending" in str(order)
    order.swiggy_order_id = "123"
    assert str(order).startswith("Swiggy order 123")


def lock_with_a_pick(lunch, member, host):
    pick(client_for(member), lunch)
    client_for(host).post(f"/api/lunches/{lunch.id}/lock")


def test_host_unlocks_locked_picks(lunch, member, host):
    lock_with_a_pick(lunch, member, host)

    response = client_for(host).post(
        f"/api/lunches/{lunch.id}/unlock", {}, format="json"
    )

    assert response.json()["status"] == "open"
    assert response.json()["orders"] == []
    assert len(response.json()["items"]) == 1


def test_unlocking_after_the_cutoff_gives_more_time(lunch, member, host):
    lock_with_a_pick(lunch, member, host)
    close_picks(lunch)

    client_for(host).post(f"/api/lunches/{lunch.id}/unlock", {}, format="json")

    lunch.refresh_from_db()
    assert lunch.cutoff_at > timezone.now() + timedelta(minutes=20)


def test_unlocking_can_set_a_new_cutoff(lunch, member, host):
    lock_with_a_pick(lunch, member, host)
    new_cutoff = timezone.now() + timedelta(hours=3)

    client_for(host).post(
        f"/api/lunches/{lunch.id}/unlock",
        {"cutoff_at": new_cutoff.isoformat()},
        format="json",
    )

    lunch.refresh_from_db()
    assert abs(lunch.cutoff_at - new_cutoff) < timedelta(seconds=1)


def test_only_a_locked_lunch_can_be_unlocked(lunch, host):
    response = client_for(host).post(
        f"/api/lunches/{lunch.id}/unlock", {}, format="json"
    )

    assert response.status_code == 409


def test_unlocking_needs_session_update(lunch, member):
    response = client_for(member).post(
        f"/api/lunches/{lunch.id}/unlock", {}, format="json"
    )

    assert response.status_code == 403


def test_a_lunch_link_opens_the_running_lunch(lunch, settings):
    settings.APP_URL = "https://lunch.example"

    assert services.lunch_link(lunch) == f"https://lunch.example/#/lunch/{lunch.id}"


def test_a_person_without_a_company_cannot_start_a_lunch(db):
    loner = make_user(None, "Loner")

    from apps.core.errors import Conflict

    with pytest.raises(Conflict):
        services.start(loner, timezone.now(), "x")


# --- background jobs, notifications, the live board and the job endpoint -------


def test_a_due_job_runs_inline_and_a_future_one_is_skipped(
    db, monkeypatch, django_capture_on_commit_callbacks, caplog
):
    ran = []
    monkeypatch.setitem(jobs.registry, "probe", ran.append)

    with django_capture_on_commit_callbacks(execute=True), caplog.at_level("INFO"):
        jobs.enqueue("probe", {"n": 1})
        jobs.enqueue("probe", {"n": 2}, at=timezone.now() + timedelta(hours=1))

    assert ran == [{"n": 1}]
    assert "skipped in local mode" in caplog.text


def test_an_inline_job_that_asks_to_retry_only_logs(
    db, monkeypatch, django_capture_on_commit_callbacks, caplog
):
    def later(payload):
        raise RetryLater("not yet")

    monkeypatch.setitem(jobs.registry, "probe", later)

    with django_capture_on_commit_callbacks(execute=True), caplog.at_level("WARNING"):
        jobs.enqueue("probe", {})

    assert "asked to retry" in caplog.text


class FakeTasksClient:
    def __init__(self, exists=False):
        self.created = []
        self.exists = exists

    def task_path(self, *parts):
        return "/".join(parts)

    def queue_path(self, *parts):
        return "/".join(parts)

    def create_task(self, parent, task):
        from google.api_core.exceptions import AlreadyExists

        if self.exists:
            raise AlreadyExists("dup")
        self.created.append((parent, task))


@pytest.mark.parametrize("exists", [False, True])
def test_with_cloud_tasks_a_job_becomes_a_task(
    db, monkeypatch, settings, django_capture_on_commit_callbacks, exists
):
    settings.CLOUD_TASKS_ENABLED = True
    settings.TASKS_BASE_URL = "https://svc.example"
    client = FakeTasksClient(exists)
    monkeypatch.setattr(jobs, "_client", lambda: client)

    with django_capture_on_commit_callbacks(execute=True):
        jobs.enqueue(
            "lock-lunch", {"a": 1}, at=timezone.now() + timedelta(hours=1), key="k"
        )
        jobs.enqueue("lock-lunch", {"a": 2})

    if not exists:
        task = client.created[0][1]
        assert (
            task["http_request"]["url"]
            == "https://svc.example/internal/tasks/lock-lunch"
        )
        assert task["name"].endswith("lock-lunch-k")
        assert "schedule_time" in task
        assert len(client.created) == 2


def test_push_and_email(settings, monkeypatch, caplog):
    from firebase_admin import messaging

    from apps.core import firebase

    notify.subscribe_to_company("tok", "org-1")  # off: nothing happens
    with caplog.at_level("INFO"):
        notify.push_to_company("org-1", "Hi", "There")
    assert "Push skipped" in caplog.text

    settings.PUSH_ENABLED = True
    sent, subscribed = [], []
    monkeypatch.setattr(firebase, "app", lambda: "app")
    monkeypatch.setattr(messaging, "send", lambda m, app: sent.append(m))
    monkeypatch.setattr(
        messaging, "subscribe_to_topic", lambda t, topic, app: subscribed.append(topic)
    )
    notify.subscribe_to_company("tok", "org-1")
    notify.push_to_company("org-1", "Hi", "There", "https://x")
    notify.push_to_company("org-1", "Hi", "There")

    assert subscribed == ["org-org-1"]
    assert [m.topic for m in sent] == ["org-org-1", "org-org-1"]
    notify.email([], "none", "none")
    notify.email(["a@acme.com"], "Subject", "Body")
    assert [m.subject for m in mail.outbox] == ["Subject"]


def test_the_live_board_is_written_after_commit(
    lunch, member, settings, monkeypatch, django_capture_on_commit_callbacks
):
    from firebase_admin import firestore

    from apps.core import firebase

    settings.FIRESTORE_ENABLED = True
    written = {}

    class Doc:
        def __init__(self, key):
            self.key = key

        def set(self, data):
            written[self.key] = data

    class Client:
        def collection(self, name):
            return self

        def document(self, key):
            return Doc(key)

    monkeypatch.setattr(firebase, "app", lambda: "app")
    monkeypatch.setattr(firestore, "client", lambda app: Client())
    pick(client_for(member), lunch)

    with django_capture_on_commit_callbacks(execute=True):
        board.publish(lunch)

    assert written[str(lunch.id)]["org_id"] == str(lunch.org_id)
    assert written[str(lunch.id)]["items"][0]["dish_name"] == "Masala Dosa"


def test_a_failed_board_write_does_not_fail_the_change(
    lunch, settings, monkeypatch, caplog
):
    from firebase_admin import firestore

    from apps.core import firebase

    def broken(app):
        raise RuntimeError("firestore is down")

    monkeypatch.setattr(firebase, "app", lambda: "app")
    monkeypatch.setattr(firestore, "client", broken)

    with caplog.at_level(logging.ERROR):
        board._write(lunch.org_id, lunch.id)

    assert "Live board update failed" in caplog.text


def test_the_job_endpoint_runs_jobs_for_cloud_tasks(
    client, settings, monkeypatch, lunch
):
    from google.oauth2 import id_token

    settings.TASKS_BASE_URL = "https://svc.example"
    settings.TASKS_SERVICE_ACCOUNT = "tasks@p.iam.gserviceaccount.com"
    claims = {"email": settings.TASKS_SERVICE_ACCOUNT, "email_verified": True}
    monkeypatch.setattr(
        id_token, "verify_oauth2_token", lambda token, req, audience: claims
    )
    auth = {"HTTP_AUTHORIZATION": "Bearer t"}
    payload = {"org_id": str(lunch.org_id), "lunch_id": str(lunch.id)}

    waiting = client.post(
        "/internal/tasks/lock-lunch", payload, content_type="application/json", **auth
    )
    close_picks(lunch)
    done = client.post(
        "/internal/tasks/lock-lunch", payload, content_type="application/json", **auth
    )
    unknown = client.post(
        "/internal/tasks/nope", {}, content_type="application/json", **auth
    )

    assert waiting.status_code == 503
    assert done.status_code == 204
    assert unknown.status_code == 404


def test_the_job_endpoint_checks_the_task_token(client, settings, monkeypatch):
    from google.oauth2 import id_token

    settings.TASKS_SERVICE_ACCOUNT = "tasks@p.iam.gserviceaccount.com"

    def forged(token, req, audience):
        raise ValueError("bad")

    post = lambda **extra: client.post(  # noqa: E731
        "/internal/tasks/lock-lunch", {}, content_type="application/json", **extra
    )
    assert post().status_code == 403
    monkeypatch.setattr(id_token, "verify_oauth2_token", forged)
    assert post(HTTP_AUTHORIZATION="Bearer t").status_code == 403
    monkeypatch.setattr(
        id_token,
        "verify_oauth2_token",
        lambda token, req, audience: {"email": "x@evil.com", "email_verified": True},
    )
    assert post(HTTP_AUTHORIZATION="Bearer t").status_code == 403


def test_the_job_endpoint_builds_its_google_client_once(monkeypatch):
    from apps.core import views

    views._google_request.cache_clear()
    assert views._google_request() is views._google_request()
