"""The host's control of the share link: close it to new people, and it ends at the
cutoff."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.lunches.models import OrderSession, SessionMember
from tests.conftest import client_for
from tests.test_parties import join, person, start

pytestmark = pytest.mark.django_db


def party():
    host = person("hana")
    lunch_id = start(host).json()["id"]
    return host, lunch_id


def set_joining(user, lunch_id, open_):
    return client_for(user).put(
        f"/api/lunches/{lunch_id}/joining", {"open": open_}, format="json"
    )


def test_a_lunch_starts_open_to_people_with_the_link():
    host, lunch_id = party()

    board = client_for(host).get(f"/api/lunches/{lunch_id}").json()

    assert board["joining_open"] is True
    assert join(person("gus"), lunch_id).status_code == 200


def test_the_host_closes_the_link_to_new_people_and_opens_it_again():
    host, lunch_id = party()

    closed = set_joining(host, lunch_id, False)
    assert closed.status_code == 200 and closed.json()["joining_open"] is False

    newcomer = person("gus")
    refused = join(newcomer, lunch_id)
    assert refused.status_code == 403
    assert "closed this lunch to new people" in refused.json()["detail"]
    assert not SessionMember.objects.exists()

    assert set_joining(host, lunch_id, True).json()["joining_open"] is True
    assert join(newcomer, lunch_id).status_code == 200


def test_closing_the_link_does_not_remove_people_already_in():
    host, lunch_id = party()
    guest = person("gus")
    join(guest, lunch_id)

    set_joining(host, lunch_id, False)

    assert join(guest, lunch_id).status_code == 200  # reopening their own link
    assert SessionMember.objects.filter(user=guest).exists()
    assert client_for(guest).get(f"/api/lunches/{lunch_id}").status_code == 200


def test_a_guest_cannot_close_the_hosts_link():
    host, lunch_id = party()
    guest = person("gus")
    join(guest, lunch_id)

    response = set_joining(guest, lunch_id, False)

    assert response.status_code in (403, 404)
    assert OrderSession.objects.get().joining_open is True


def test_a_stranger_cannot_close_someone_elses_link():
    _, lunch_id = party()

    assert set_joining(person("sam"), lunch_id, False).status_code in (403, 404)
    assert OrderSession.objects.get().joining_open is True


def test_the_link_stops_working_at_the_cutoff():
    host, lunch_id = party()
    OrderSession.objects.update(cutoff_at=timezone.now() - timedelta(minutes=1))

    refused = join(person("gus"), lunch_id)

    assert refused.status_code == 400
    assert refused.json()["detail"] == "This lunch has closed."


def test_once_picks_lock_there_is_no_link_left_to_close(host, lunch):
    OrderSession.objects.filter(id=lunch.id).update(status="locked")

    response = set_joining(host, lunch.id, False)

    assert response.status_code == 409
    assert "nobody new can join anyway" in response.json()["detail"]


def test_a_company_member_without_the_permission_cannot_close_the_link(member, lunch):
    assert set_joining(member, lunch.id, False).status_code == 403


def test_the_open_flag_is_in_the_lunch_list_too(host, lunch):
    listed = client_for(host).get("/api/lunches").json()

    assert listed[0]["joining_open"] is True
