"""Rate limits: one person can't hammer the API or burn what it calls."""

import pytest
from django.core.cache import caches
from django.db import connection
from django.test import override_settings
from rest_framework.throttling import SimpleRateThrottle

from apps.core import throttling
from tests.conftest import client_for, make_user
from tests.test_parties import person
from tests.test_places import PHOTON, serve


@pytest.fixture(autouse=True)
def fresh_counts():
    caches["throttle"].clear()
    yield
    caches["throttle"].clear()


@pytest.fixture
def rates(monkeypatch):
    """Set the rates for a test (they are read when a request starts)."""

    def use(**scopes):
        for scope, rate in scopes.items():
            monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, scope, rate)

    return use


def test_the_place_search_is_limited_per_person(
    member, other_company, monkeypatch, rates
):
    serve(monkeypatch, photon=PHOTON)
    rates(places="3/min")
    client = client_for(member)
    other = client_for(make_user(other_company, "Olga"))

    used = [client.get("/api/places", {"q": "infopark"}).status_code for _ in range(3)]
    # someone else has their own allowance (checked before the 429: DRF marks the test's
    # wrapping transaction for rollback when it answers with an error)
    elsewhere = other.get("/api/places", {"q": "infopark"}).status_code

    assert used == [200, 200, 200]
    assert elsewhere == 200
    assert client.get("/api/places", {"q": "infopark"}).status_code == 429


def test_a_refused_request_says_when_to_try_again(member, monkeypatch, rates):
    serve(monkeypatch, photon=PHOTON)
    rates(places="1/min")
    client = client_for(member)
    client.get("/api/places", {"q": "infopark"})

    response = client.get("/api/places", {"q": "infopark"})

    assert response.status_code == 429
    assert 0 < int(response["Retry-After"]) <= 60
    assert "try again in" in response.json()["detail"].lower()


def test_restaurant_browsing_has_its_own_limit(member, monkeypatch, rates):
    from apps.catalog import swiggy

    monkeypatch.setattr(swiggy, "nearby_restaurants", lambda lat, lng: [])
    rates(catalog="2/min")
    client = client_for(member)

    codes = [
        client.get("/api/restaurants", {"lat": 10.0, "lng": 76.3}).status_code
        for _ in range(3)
    ]

    assert codes == [200, 200, 429]


def test_everything_else_is_limited_per_person_too(member, rates):
    rates(user="3/min")
    client = client_for(member)

    codes = [client.get("/api/me").status_code for _ in range(4)]

    assert codes == [200, 200, 200, 429]


def test_joining_by_link_is_limited(db, rates):
    rates(join="2/min")
    guest = client_for(person("gus"))
    missing = "123e4567-e89b-42d3-a456-426614174000"

    codes = [guest.post(f"/api/lunches/{missing}/join").status_code for _ in range(3)]

    assert codes == [404, 404, 429]


def test_an_access_token_has_the_same_limits_as_its_person(member, rates):
    from tests.test_api_tokens import make_token, with_token

    token = make_token(member)["token"]
    caches["throttle"].clear()  # making the token counted as one request
    rates(user="2/min")

    codes = [with_token(token).get("/api/me").status_code for _ in range(3)]

    assert codes == [200, 200, 429]


@override_settings(
    CACHES={
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "throttle": {
            "BACKEND": "apps.core.throttling.FailOpenDatabaseCache",
            "LOCATION": "django_throttle_cache",
        },
    }
)
def test_the_shared_database_cache_counts_across_instances(member, rates):
    """With THROTTLE_CACHE=database the count lives in the database, so every server
    instance sees it (the table is made by a migration)."""
    rates(user="2/min")
    client = client_for(member)

    codes = [client.get("/api/me").status_code for _ in range(3)]

    assert codes == [200, 200, 429]
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM django_throttle_cache")
        assert cursor.fetchone()[0] >= 1


@override_settings(
    CACHES={
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "throttle": {
            "BACKEND": "apps.core.throttling.FailOpenDatabaseCache",
            "LOCATION": "no_such_cache_table",
        },
    }
)
def test_a_broken_rate_limit_cache_never_breaks_requests(member, rates, caplog):
    rates(user="1/min")
    client = client_for(member)

    codes = [client.get("/api/me").status_code for _ in range(3)]

    assert codes == [200, 200, 200]  # limit skipped, the request still works
    assert "not limiting" in caplog.text


def test_the_cache_class_is_the_one_the_throttles_use():
    assert throttling.UserThrottle.cache.get("nothing") is None


def test_cloud_tasks_calling_the_job_endpoint_is_never_limited(db, rates, monkeypatch):
    """A job refused for being too frequent would not run: no lunch would lock."""
    from rest_framework.test import APIClient

    from apps.core import jobs
    from apps.core import views as core_views

    class System:  # what the job endpoint's authentication yields: no id to count
        is_authenticated = True

    monkeypatch.setattr(
        core_views.CloudTasksAuthentication,
        "authenticate",
        lambda self, request: (System(), None),
    )
    ran = []
    monkeypatch.setitem(jobs.registry, "probe", lambda payload: ran.append(payload))
    rates(user="1/min")

    codes = [
        APIClient().post("/internal/tasks/probe", {}, format="json").status_code
        for _ in range(4)
    ]

    assert codes == [204, 204, 204, 204]
    assert len(ran) == 4


def test_a_request_with_no_person_behind_it_is_not_counted(db):
    class Anonymous:
        pass

    class Request:
        user = Anonymous()

    assert throttling.UserThrottle().get_cache_key(Request(), None) is None
    assert throttling.ScopedThrottle().get_cache_key(Request(), None) is None
