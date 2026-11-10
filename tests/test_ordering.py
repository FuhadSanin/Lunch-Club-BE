import logging
import uuid
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.core import jobs
from apps.core.errors import Conflict, RetryLater
from apps.lunches import services as lunch_services
from apps.lunches.models import (
    OrderItem,
    OrderStatus,
    SessionStatus,
    SwiggyOrder,
)
from apps.ordering import jobs as ordering_jobs
from apps.ordering import services
from apps.ordering.progress import Progress
from apps.ordering.providers import get_provider
from apps.ordering.providers.base import HostActionRequired, PlaceRequest
from apps.ordering.providers.manual import ManualProvider
from tests.conftest import client_for, make_user


def locked_lunch(lunch, member, restaurants=("Biryani Life",), restaurant_ids=True):
    """Everyone's picks are in and locked: one Swiggy order per MAX_RESTAURANTS."""
    for number, name in enumerate(restaurants):
        OrderItem.objects.create(
            session=lunch,
            user=member,
            restaurant_name=name,
            restaurant_id=str(100 + number) if restaurant_ids else "",
            dish_name=f"Dish {number}",
            quantity=2,
            price_paise=15000,
            note="no onion",
        )
    lunch_services.lock(lunch)
    lunch.refresh_from_db()
    return lunch


def confirm(client, lunch, capture):
    with capture(execute=True):
        return client.post(f"/api/lunches/{lunch.id}/confirm")


def mark_interrupted(order, minutes_ago):
    """As if an attempt crashed mid-checkout `minutes_ago` minutes ago."""
    SwiggyOrder.objects.filter(id=order.id).update(
        status=OrderStatus.PLACING,
        updated_at=timezone.now() - timedelta(minutes=minutes_ago),
    )


@pytest.fixture
def one_restaurant_each(settings):
    settings.MAX_RESTAURANTS_PER_ORDER = 1


def test_only_a_locked_lunch_can_be_confirmed(host, lunch, fake_provider):
    response = client_for(host).post(f"/api/lunches/{lunch.id}/confirm")

    assert response.status_code == 409


def test_confirming_needs_review_create(member, lunch, host):
    assert client_for(member).post(f"/api/lunches/{lunch.id}/confirm").status_code == (
        403
    )


def test_confirming_places_each_order_once(
    host, member, lunch, fake_provider, django_capture_on_commit_callbacks
):
    locked_lunch(lunch, member)
    client = client_for(host)

    response = confirm(client, lunch, django_capture_on_commit_callbacks)

    assert response.status_code == 200
    order = SwiggyOrder.objects.get()
    assert order.status == OrderStatus.PLACED
    assert order.swiggy_order_id == f"SW-{order.id.hex[:8]}"
    lunch.refresh_from_db()
    assert lunch.status == SessionStatus.PLACED
    # Placing again (a duplicate job) does nothing.
    services.place(lunch.org_id, order.id)
    assert fake_provider.calls.count("place") == 1


def test_without_automatic_ordering_the_host_is_asked_to_finish(
    host, member, lunch, settings, django_capture_on_commit_callbacks
):
    settings.ORDER_PROVIDER = "apps.ordering.providers.manual.ManualProvider"
    get_provider.cache_clear()
    locked_lunch(lunch, member)

    confirm(client_for(host), lunch, django_capture_on_commit_callbacks)

    order = SwiggyOrder.objects.get()
    assert order.status == OrderStatus.NEEDS_HOST
    assert "Swiggy order number" in order.last_error
    assert any(host.email in message.to for message in mail.outbox)
    get_provider.cache_clear()


def test_host_records_the_swiggy_order_then_delivery(
    host, member, lunch, settings, django_capture_on_commit_callbacks
):
    settings.ORDER_PROVIDER = "apps.ordering.providers.manual.ManualProvider"
    get_provider.cache_clear()
    locked_lunch(lunch, member)
    client = client_for(host)
    confirm(client, lunch, django_capture_on_commit_callbacks)
    order = SwiggyOrder.objects.get()

    placed = client.post(
        f"/api/orders/{order.id}/placed", {"swiggy_order_id": " 77 "}, format="json"
    )
    delivered = client.post(f"/api/orders/{order.id}/delivered")

    assert placed.status_code == 200
    assert delivered.status_code == 200
    order.refresh_from_db()
    assert order.swiggy_order_id == "77"
    assert order.status == OrderStatus.DELIVERED
    lunch.refresh_from_db()
    assert lunch.status == SessionStatus.DELIVERED
    get_provider.cache_clear()


def test_host_cannot_record_an_order_that_is_not_waiting_for_them(
    host, member, lunch, fake_provider
):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    client = client_for(host)

    assert (
        client.post(f"/api/orders/{order.id}/placed", {}, format="json").status_code
        == 409
    )
    assert client.post(f"/api/orders/{order.id}/delivered").status_code == 409


def test_another_companys_order_is_not_found(
    host, member, lunch, other_company, fake_provider
):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    stranger = make_user(other_company, "Gina", host.permissions)

    response = client_for(stranger).post(f"/api/orders/{order.id}/delivered")

    assert response.status_code == 404
    assert client_for(host).post(f"/api/orders/{uuid.uuid4()}/retry").status_code == 404


def test_a_failed_attempt_hands_the_order_to_the_host(
    host, member, lunch, fake_provider, caplog
):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    fake_provider.error = RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        services.place(lunch.org_id, order.id)

    order.refresh_from_db()
    assert order.status == OrderStatus.NEEDS_HOST
    assert "Automatic ordering failed" in order.last_error
    assert mail.outbox[-1].subject == "Your lunch order needs you"
    # A reason the provider gives is passed on word for word.
    fake_provider.error = HostActionRequired("Log in to Swiggy first.")
    SwiggyOrder.objects.filter(id=order.id).update(status=OrderStatus.PENDING)
    services.place(lunch.org_id, order.id)
    order.refresh_from_db()
    assert order.last_error == "Log in to Swiggy first."


def test_a_retry_while_an_attempt_is_running_waits(host, member, lunch, fake_provider):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    mark_interrupted(order, minutes_ago=1)

    with pytest.raises(RetryLater):
        services.place(lunch.org_id, order.id)

    assert fake_provider.calls == []


def test_a_retry_after_a_crash_checks_swiggy_before_ordering_again(
    host, member, lunch, fake_provider
):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    mark_interrupted(order, minutes_ago=30)
    fake_provider.existing = type("Found", (), {"swiggy_order_id": "SW-FOUND"})()

    services.place(lunch.org_id, order.id)

    order.refresh_from_db()
    assert fake_provider.calls[0] == "find_existing"
    assert "place" not in fake_provider.calls
    assert order.swiggy_order_id == "SW-FOUND"

    # Nothing found: the order is placed after all.
    again = SwiggyOrder.objects.create(
        session=lunch, restaurant_count=1, total_paise=100
    )
    mark_interrupted(again, minutes_ago=30)
    fake_provider.existing = None
    services.place(lunch.org_id, again.id)
    assert "place" in fake_provider.calls


def test_delivery_tracking_marks_the_lunch_delivered(
    host, member, lunch, fake_provider, django_capture_on_commit_callbacks
):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    services.place(lunch.org_id, order.id)

    services.poll_delivery(lunch.org_id, order.id, 1)

    order.refresh_from_db()
    assert order.status == OrderStatus.DELIVERED
    lunch.refresh_from_db()
    assert lunch.status == SessionStatus.DELIVERED
    # Polling a delivered order is a no-op.
    services.poll_delivery(lunch.org_id, order.id, 2)


def test_delivery_polls_again_until_the_limit(
    host, member, lunch, fake_provider, monkeypatch
):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    services.place(lunch.org_id, order.id)
    fake_provider.is_delivered = lambda swiggy_order_id: False
    queued = []
    monkeypatch.setattr(jobs, "enqueue", lambda *a, **k: queued.append((a, k)))

    services.poll_delivery(lunch.org_id, order.id, 3)
    services.poll_delivery(lunch.org_id, order.id, services.MAX_DELIVERY_POLLS)

    assert len(queued) == 1
    assert queued[0][0][1]["poll"] == 4


def test_ordering_jobs_run_the_services(
    host, member, lunch, fake_provider, monkeypatch
):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    payload = {"org_id": str(lunch.org_id), "order_id": str(order.id)}

    ordering_jobs.place_order(payload)
    ordering_jobs.poll_delivery({**payload, "poll": 1})

    order.refresh_from_db()
    assert order.status == OrderStatus.DELIVERED


def test_job_endpoint_rejects_callers_without_a_task_token(db, client):
    response = client.post("/internal/tasks/place-order", {}, format="json")

    assert response.status_code in (401, 403)
    bad = client.post(
        "/internal/tasks/place-order",
        {},
        HTTP_AUTHORIZATION="Bearer nonsense",
    )
    assert bad.status_code in (401, 403)


def task_token(monkeypatch, settings, claims=None, error=None):
    from google.oauth2 import id_token

    settings.TASKS_BASE_URL = "https://tasks.example"
    settings.TASKS_SERVICE_ACCOUNT = "tasks@example.iam"

    def verify(token, request, audience=None):
        if error:
            raise error
        return claims

    monkeypatch.setattr(id_token, "verify_oauth2_token", verify)


def test_the_job_endpoint_runs_jobs_for_cloud_tasks(db, client, monkeypatch, settings):
    task_token(
        monkeypatch, settings, {"email": "tasks@example.iam", "email_verified": True}
    )
    seen = []
    monkeypatch.setitem(jobs.registry, "probe", seen.append)

    ok = client.post(
        "/internal/tasks/probe",
        '{"a": 1}',
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer t",
    )
    missing = client.post(
        "/internal/tasks/nope",
        {},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer t",
    )

    assert ok.status_code == 204
    assert seen == [{"a": 1}]
    assert missing.status_code == 404

    def later(payload):
        raise RetryLater("not yet")

    monkeypatch.setitem(jobs.registry, "later", later)
    retry = client.post(
        "/internal/tasks/later",
        {},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer t",
    )
    assert retry.status_code == 503


def test_the_job_endpoint_refuses_other_identities(db, client, monkeypatch, settings):
    task_token(
        monkeypatch, settings, {"email": "someone@else.com", "email_verified": True}
    )
    wrong = client.post(
        "/internal/tasks/place-order",
        {},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer t",
    )
    task_token(monkeypatch, settings, error=ValueError("forged"))
    forged = client.post(
        "/internal/tasks/place-order",
        {},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer t",
    )

    assert wrong.status_code in (401, 403)
    assert forged.status_code in (401, 403)


def test_the_host_can_retry_an_order_that_needs_them(
    host, member, lunch, fake_provider, django_capture_on_commit_callbacks
):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    fake_provider.error = RuntimeError("down")
    services.place(lunch.org_id, order.id)
    fake_provider.error = None

    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(host).post(f"/api/orders/{order.id}/retry")

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.PLACED
    assert order.last_error == ""


def test_only_an_order_waiting_for_the_host_can_be_retried(
    host, member, lunch, fake_provider
):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()

    assert client_for(host).post(f"/api/orders/{order.id}/retry").status_code == 409


@pytest.mark.django_db(transaction=True)
def test_steps_are_saved_even_while_the_browsers_event_loop_is_running(
    host, member, lunch, fake_provider
):
    import asyncio

    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    progress = Progress(
        lambda steps: services._save_steps(lunch.org_id, order.id, steps)
    )
    loop = asyncio.new_event_loop()
    asyncio.events._set_running_loop(loop)
    try:
        progress.plan([("browser", "Open Chrome", "to order")])
        with progress.step("browser"):
            pass
    finally:
        asyncio.events._set_running_loop(None)
        loop.close()

    order.refresh_from_db()
    assert order.bot_steps[0]["status"] == "done"
    assert order.bot_steps[0]["why"] == "to order"


def test_a_step_that_cannot_be_saved_does_not_hide_the_real_error(caplog):
    def broken(steps):
        raise OSError("db down")

    progress = Progress(broken)
    progress.plan([("a", "A", "why"), ("b", "B", "why")])

    with pytest.raises(ValueError, match="real"), progress.step("a"):
        raise ValueError("real problem\nmore")

    assert progress.steps[0]["status"] == "failed"
    assert progress.steps[0]["detail"] == "ValueError: real problem"
    assert progress.steps[1]["status"] == "pending"


def test_progress_steps_report_what_happened():
    progress = Progress()
    progress.plan([("a", "A", "x"), ("b", "B", "y"), ("c", "C", "z")])

    with progress.step("a"):
        pass
    progress.start("b")
    progress.done("b", "all good")
    with pytest.raises(HostActionRequired), progress.step("c"):
        raise HostActionRequired("Please log in")
    progress.fail("a", "later")

    statuses = [(s["status"], s["detail"]) for s in progress.steps]
    assert statuses == [
        ("failed", "later"),
        ("done", "all good"),
        ("failed", "Please log in"),
    ]


def test_the_manual_provider_always_hands_over_to_the_host():
    provider = ManualProvider()
    request = PlaceRequest(uuid.uuid4(), uuid.uuid4(), "addr", (), 0)

    with pytest.raises(HostActionRequired):
        provider.place(request, Progress())
    assert provider.find_existing(request) is None
    assert provider.tracks_delivery is False
    with pytest.raises(NotImplementedError):
        provider.is_delivered("x")


def test_the_second_restaurant_waits_until_the_first_is_ordered(
    host,
    member,
    lunch,
    fake_provider,
    one_restaurant_each,
    django_capture_on_commit_callbacks,
):
    fake_provider.sequential = True
    fake_provider.tracks_delivery = False
    locked_lunch(lunch, member, restaurants=("A Kitchen", "B Kitchen"))
    first, second = SwiggyOrder.objects.order_by("created_at")
    fake_provider.error = HostActionRequired("Check out in Swiggy")

    services.place(lunch.org_id, second.id)
    second.refresh_from_db()
    assert second.status == OrderStatus.PENDING
    assert second.last_error.startswith("Waiting for the previous")
    assert fake_provider.calls == []

    services.place(lunch.org_id, first.id)
    first.refresh_from_db()
    assert first.status == OrderStatus.NEEDS_HOST

    fake_provider.error = None
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(host).post(
            f"/api/orders/{first.id}/placed", {}, format="json"
        )

    assert response.status_code == 200
    second.refresh_from_db()
    assert second.status == OrderStatus.PLACED


def test_orders_are_not_held_back_when_the_provider_is_not_sequential(
    host, member, lunch, fake_provider, one_restaurant_each
):
    locked_lunch(lunch, member, restaurants=("A Kitchen", "B Kitchen"))
    first, second = SwiggyOrder.objects.order_by("created_at")

    services.place(lunch.org_id, second.id)

    second.refresh_from_db()
    assert second.status == OrderStatus.PLACED


def test_placing_an_unknown_or_finished_order_does_nothing(
    host, member, lunch, fake_provider
):
    services.place(lunch.org_id, uuid.uuid4())
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.get()
    SwiggyOrder.objects.filter(id=order.id).update(status=OrderStatus.PLACED)

    services.place(lunch.org_id, order.id)

    assert fake_provider.calls == []


def test_confirm_refuses_a_lunch_that_is_not_locked(lunch):
    with pytest.raises(Conflict):
        services.confirm(lunch)


def test_the_place_request_lists_the_dishes(host, member, lunch):
    locked_lunch(lunch, member)
    order = SwiggyOrder.objects.select_related("session").get()

    request = services._place_request(order)

    assert request.total_paise == 30000
    line = request.lines[0]
    assert (line.restaurant_name, line.quantity, line.restaurant_id) == (
        "Biryani Life",
        2,
        "100",
    )
    assert line.note == "no onion"


def test_the_bot_status_endpoint_needs_order_update(host, member, db):
    assert client_for(member).get("/api/bot/status").status_code == 403
    response = client_for(host).get("/api/bot/status")
    assert response.status_code == 200
    assert "checks" in response.json()
