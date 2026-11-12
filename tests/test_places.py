import httpx
import pytest
from django.core.cache import cache

from apps.accounts.models import User
from apps.catalog import places
from tests.conftest import client_for, make_user

PHOTON = {
    "features": [
        {
            "properties": {
                "osm_type": "W",
                "osm_id": 731817480,
                "name": "Infopark Kochi",
                "district": "Edachira",
                "city": "Ernakulam",
                "state": "Kerala",
            },
            "geometry": {"type": "Point", "coordinates": [76.3628106, 10.0105236]},
        },
        {
            "properties": {
                "osm_type": "N",
                "osm_id": 9399375060,
                "name": "Infopark",
                "street": "Kakkanad-InfoPark-Karimugal Road",
                "city": "Ernakulam",
                "state": "Kerala",
                "postcode": "682042",
            },
            "geometry": {"type": "Point", "coordinates": [76.3635168, 10.0152584]},
        },
        {  # the same place again, and one with no coordinates: both are dropped
            "properties": {"osm_type": "W", "osm_id": 731817480, "name": "Infopark"},
            "geometry": {"type": "Point", "coordinates": [76.36, 10.01]},
        },
        {"properties": {"osm_type": "W", "osm_id": 5, "name": "Broken"}},
    ]
}
NOMINATIM = [
    {
        "lat": "10.0105236",
        "lon": "76.3628106",
        "display_name": "Infopark Kochi, Edachira, Ernakulam, Kerala, India",
    }
]


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def osm(monkeypatch):
    """Stand in for Photon and Nominatim; `osm.calls` lists the requests made."""
    cache.clear()
    calls: list[httpx.Request] = []
    state = {"fail": False, "lookup": NOMINATIM}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if state["fail"]:
            return httpx.Response(503)
        if "photon" in request.url.host:
            return httpx.Response(200, json=PHOTON)
        return httpx.Response(200, json=state["lookup"])

    monkeypatch.setattr(places, "transport", httpx.MockTransport(handler))
    handler.calls = calls
    handler.state = state
    return handler


def test_typing_an_address_suggests_places(company, member, osm):
    response = client_for(member).get("/api/places", {"q": "infopark"})

    assert response.status_code == 200
    assert response.json() == [
        {
            "place_id": "W731817480",
            "description": "Infopark Kochi, Edachira, Ernakulam, Kerala",
            "main_text": "Infopark Kochi",
            "secondary_text": "Edachira, Ernakulam, Kerala",
            "lat": 10.0105236,
            "lng": 76.3628106,
        },
        {
            "place_id": "N9399375060",
            "description": (
                "Infopark, Kakkanad-InfoPark-Karimugal Road, Ernakulam, Kerala, 682042"
            ),
            "main_text": "Infopark",
            "secondary_text": (
                "Kakkanad-InfoPark-Karimugal Road, Ernakulam, Kerala, 682042"
            ),
            "lat": 10.0152584,
            "lng": 76.3635168,
        },
    ]


def test_suggestions_stay_in_india_and_lean_towards_the_office(member, osm):
    client_for(member).get("/api/places", {"q": "infopark", "lat": 10.0, "lng": 76.3})

    params = osm.calls[0].url.params
    assert params["bbox"] == places.INDIA_BBOX
    assert params["lat"] == "10.0"
    assert params["lon"] == "76.3"
    assert "lunch-backend" in osm.calls[0].headers["user-agent"]


def test_suggestions_are_cached(member, osm):
    for _ in range(3):
        client_for(member).get("/api/places", {"q": "Infopark"})

    assert len(osm.calls) == 1


def test_choosing_a_suggestion_gives_its_address_and_coordinates(member, osm):
    client_for(member).get("/api/places", {"q": "infopark"})

    response = client_for(member).get("/api/places/W731817480")

    assert response.json() == {
        "place_id": "W731817480",
        "address": "Infopark Kochi, Edachira, Ernakulam, Kerala",
        "lat": 10.0105236,
        "lng": 76.3628106,
    }
    assert len(osm.calls) == 1  # remembered from the suggestions


def test_a_place_not_suggested_here_is_looked_up(member, osm):
    response = client_for(member).get("/api/places/W731817480")

    assert response.json()["address"] == NOMINATIM[0]["display_name"]
    assert response.json()["lat"] == 10.0105236
    assert osm.calls[0].url.params["osm_ids"] == "W731817480"
    client_for(member).get("/api/places/W731817480")
    assert len(osm.calls) == 1


def test_an_unknown_or_malformed_place_is_not_found(member, osm):
    osm.state["lookup"] = []

    assert client_for(member).get("/api/places/W1").status_code == 404
    assert client_for(member).get("/api/places/not-a-place").status_code == 404
    assert len(osm.calls) == 1  # the malformed id never leaves the server


def test_an_unreadable_lookup_is_a_bad_gateway(member, osm):
    osm.state["lookup"] = [{"nothing": "useful"}]

    assert client_for(member).get("/api/places/W1").status_code == 502


def test_typing_needs_two_letters(member, osm):
    response = client_for(member).get("/api/places", {"q": "i"})

    assert response.status_code == 400
    assert not osm.calls


def test_two_letters_are_enough_to_suggest(member, osm):
    assert client_for(member).get("/api/places", {"q": "in"}).status_code == 200


def test_the_place_service_failing_is_a_bad_gateway(member, osm):
    osm.state["fail"] = True

    response = client_for(member).get("/api/places", {"q": "infopark"})

    assert response.status_code == 502
    assert "Place search" in response.json()["detail"]


def test_the_place_service_being_unreachable_is_a_bad_gateway(member, monkeypatch):
    cache.clear()

    def refuse(request):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(places, "transport", httpx.MockTransport(refuse))

    assert client_for(member).get("/api/places", {"q": "infopark"}).status_code == 502


def test_an_answer_that_is_not_json_is_a_bad_gateway(member, monkeypatch):
    cache.clear()
    monkeypatch.setattr(
        places,
        "transport",
        httpx.MockTransport(lambda request: httpx.Response(200, text="<html>")),
    )

    assert client_for(member).get("/api/places", {"q": "infopark"}).status_code == 502


def test_people_with_a_public_email_can_search_places_without_a_company(db, osm):
    gmail = make_user(None, "Lone")

    assert client_for(gmail).get("/api/places", {"q": "infopark"}).status_code == 200
    assert client_for(gmail).get("/api/places/W731817480").status_code == 200


def test_a_company_person_without_a_company_cannot_search_places(db, osm):
    loner = User.objects.create(
        email="lone@startup.io", firebase_uid="uid-lone", name="Lone"
    )

    assert client_for(loner).get("/api/places", {"q": "infopark"}).status_code == 403


def test_places_need_sign_in(client):
    assert client.get("/api/places", {"q": "infopark"}).status_code == 401


OPEN_METEO = {
    "results": [
        {
            "id": 11073561,
            "name": "Kakkanad",
            "latitude": 10.01636,
            "longitude": 76.34168,
            "admin1": "Kerala",
            "admin2": "Ernakulam District",
            "admin3": "Kanayannur",
        },
        {"id": "bad", "name": "Broken"},
    ]
}
GEOAPIFY = {
    "results": [
        {
            "place_id": "abc123",
            "formatted": "Infopark, Kakkanad, Kochi, Kerala, India",
            "address_line1": "Infopark",
            "address_line2": "Kakkanad, Kochi, Kerala, India",
            "lat": 10.0152,
            "lon": 76.3635,
        },
        {"place_id": "nope"},
    ]
}


def serve(monkeypatch, **hosts):
    """Answer by host: a JSON body, or an int status; anything unlisted is a 503."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        for host, answer in hosts.items():
            if host in request.url.host:
                if isinstance(answer, int):
                    return httpx.Response(answer)
                return httpx.Response(200, json=answer)
        return httpx.Response(503)

    monkeypatch.setattr(places, "transport", httpx.MockTransport(handler))
    return calls


def test_when_photon_is_down_towns_still_come_from_open_meteo(member, monkeypatch):
    calls = serve(monkeypatch, photon=503, **{"open-meteo": OPEN_METEO})

    response = client_for(member).get("/api/places", {"q": "kakkanad"})

    assert response.status_code == 200
    assert response.json() == [
        {
            "place_id": "M11073561",
            "description": "Kakkanad, Kanayannur, Ernakulam District, Kerala",
            "main_text": "Kakkanad",
            "secondary_text": "Kanayannur, Ernakulam District, Kerala",
            "lat": 10.01636,
            "lng": 76.34168,
        }
    ]
    assert [c.url.host for c in calls][0].startswith("photon")


def test_a_provider_that_failed_is_skipped_for_a_while(member, monkeypatch):
    calls = serve(monkeypatch, photon=503, **{"open-meteo": OPEN_METEO})
    client = client_for(member)

    client.get("/api/places", {"q": "kakkanad"})
    client.get("/api/places", {"q": "kakkanadu"})

    assert [c.url.host for c in calls].count("photon.komoot.io") == 1


def test_a_geoapify_key_makes_it_the_first_choice(member, monkeypatch, settings):
    settings.GEOAPIFY_API_KEY = "key-1"
    calls = serve(monkeypatch, geoapify=GEOAPIFY)

    response = client_for(member).get(
        "/api/places", {"q": "infopark", "lat": 10.0, "lng": 76.3}
    )

    assert response.json() == [
        {
            "place_id": "abc123",
            "description": "Infopark, Kakkanad, Kochi, Kerala, India",
            "main_text": "Infopark",
            "secondary_text": "Kakkanad, Kochi, Kerala, India",
            "lat": 10.0152,
            "lng": 76.3635,
        }
    ]
    params = calls[0].url.params
    assert params["apiKey"] == "key-1"
    assert params["bias"] == "proximity:76.3,10.0"
    assert params["filter"] == "countrycode:in"


def test_geoapify_failing_falls_back_to_the_free_services(
    member, monkeypatch, settings
):
    settings.GEOAPIFY_API_KEY = "key-1"
    serve(monkeypatch, geoapify=401, photon=PHOTON)

    response = client_for(member).get("/api/places", {"q": "infopark"})

    assert response.status_code == 200
    assert response.json()[0]["place_id"] == "W731817480"


def test_a_place_from_another_provider_is_chosen_without_a_second_call(
    member, monkeypatch
):
    calls = serve(monkeypatch, photon=503, **{"open-meteo": OPEN_METEO})
    client = client_for(member)
    client.get("/api/places", {"q": "kakkanad"})
    made = len(calls)

    chosen = client.get("/api/places/M11073561")

    assert chosen.status_code == 200
    assert chosen.json()["lat"] == 10.01636
    assert len(calls) == made
    assert client.get("/api/places/M999").status_code == 404  # not one we suggested
