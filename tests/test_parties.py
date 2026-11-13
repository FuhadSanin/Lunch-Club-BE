"""Personal parties: public-email hosts, lunches joined by link, and who sees what."""

import importlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.utils import timezone

from apps.accounts import services as accounts
from apps.accounts.models import ALL_PERMISSIONS, Organization, OrgKind, User
from apps.accounts.services import user_for_token
from apps.core import tenancy
from apps.lunches.models import OrderItem, OrderSession, SessionMember, SessionStatus
from tests.conftest import client_for, make_user

DISH = {
    "restaurant_name": "Meghana Foods",
    "restaurant_id": "1234",
    "dish_name": "Chicken Biryani",
    "quantity": 1,
    "price_paise": 32000,
}


def person(name, domain="gmail.com"):
    return user_for_token(
        {
            "uid": f"uid-{name}-{domain}",
            "email": f"{name}@{domain}",
            "email_verified": True,
            "name": name.title(),
        }
    )


def start(user, **extra):
    cutoff = timezone.now() + timedelta(hours=1)
    return client_for(user).post(
        "/api/lunches",
        {"cutoff_at": cutoff.isoformat(), "delivery_address": "Home", **extra},
        format="json",
    )


def pick(user, lunch_id, **overrides):
    return client_for(user).post(
        f"/api/lunches/{lunch_id}/items", {**DISH, **overrides}, format="json"
    )


def join(user, lunch_id):
    return client_for(user).post(f"/api/lunches/{lunch_id}/join")


def arrived(party):
    OrderSession.objects.filter(id=party.lunch_id).update(
        status=SessionStatus.DELIVERED
    )


def finished(party):
    """The food arrived and the host finished the last step, which closes it."""
    OrderSession.objects.filter(id=party.lunch_id).update(
        status=SessionStatus.DELIVERED, closed_at=timezone.now()
    )


@pytest.fixture
def party(db):
    """Asha's personal party, with one lunch started."""
    asha = person("asha")
    response = start(
        asha, name="Friday biryani", delivery_lat=10.01, delivery_lng=76.36
    )
    assert response.status_code == 201
    return SimpleNamespace(
        host=asha, lunch_id=response.json()["id"], board=response.json()
    )


# --- hosting ------------------------------------------------------------------


def test_a_public_email_starts_a_lunch_in_a_personal_party_made_for_them(db):
    asha = person("asha")
    assert asha.org is None

    response = start(
        asha, name="Friday biryani", delivery_lat=10.01, delivery_lng=76.36
    )

    assert response.status_code == 201
    board = response.json()
    assert board["name"] == "Friday biryani"
    assert board["org_kind"] == "personal"
    assert board["joined"] is False
    assert board["host"]["name"] == "Asha"
    assert board["delivery_address"] == "Home"
    assert board["delivery_lat"] == 10.01
    assert board["delivery_lng"] == 76.36
    asha.refresh_from_db()
    assert asha.org.kind == OrgKind.PERSONAL
    assert asha.org.name == "Asha's party"
    assert asha.org.email_domain == ""
    assert set(asha.permissions) == ALL_PERMISSIONS
    me = client_for(asha).get("/api/me").json()
    assert me["org"]["kind"] == "personal"
    assert me["public_email"] is True


def test_a_second_lunch_reuses_the_same_party(party):
    finished(party)
    again = start(party.host)

    assert again.status_code == 201
    assert Organization.objects.filter(kind=OrgKind.PERSONAL).count() == 1


def test_a_lunch_needs_both_coordinates_or_neither(db):
    response = start(person("asha"), delivery_lat=10.0)

    assert response.status_code == 400


def test_a_lunch_name_is_optional(db):
    assert start(person("asha")).json()["name"] == ""


def test_a_company_member_cannot_start_a_lunch(company, member):
    assert start(member).status_code == 403


def test_a_joined_company_member_cannot_start_a_lunch_either(company, member, party):
    join(member, party.lunch_id)

    assert start(member).status_code == 403


def test_two_public_email_hosts_never_see_each_others_lunches(party):
    bela = person("bela")
    own = start(bela).json()["id"]

    assert client_for(bela).get(f"/api/lunches/{party.lunch_id}").status_code == 404
    assert pick(bela, party.lunch_id).status_code == 404
    listed = {x["id"] for x in client_for(bela).get("/api/lunches").json()}
    assert listed == {own}


def test_a_company_person_without_a_company_cannot_get_a_party(db):
    outsider = person("someone", "startup.io")

    with pytest.raises(Exception, match="company"):
        accounts.create_personal_org(outsider)


def test_a_personal_party_cannot_invite_people(party):
    response = client_for(party.host).post(
        "/api/company/invites", {"emails": ["x@gmail.com"]}, format="json"
    )

    assert response.status_code == 409


# --- joining ------------------------------------------------------------------


def test_anyone_can_join_a_personal_party_lunch_from_its_link(party):
    guest = person("guest")

    response = join(guest, party.lunch_id)

    assert response.status_code == 200
    assert response.json()["joined"] is True
    assert response.json()["name"] == "Friday biryani"
    assert SessionMember.objects.filter(user=guest).count() == 1


def test_joining_twice_is_harmless(party):
    guest = person("guest")

    join(guest, party.lunch_id)
    again = join(guest, party.lunch_id)

    assert again.status_code == 200
    assert SessionMember.objects.filter(user=guest).count() == 1


def test_the_host_joining_their_own_lunch_changes_nothing(party):
    response = join(party.host, party.lunch_id)

    assert response.status_code == 200
    assert response.json()["joined"] is False
    assert not SessionMember.objects.exists()


def test_a_joined_lunch_is_listed_and_marked_as_joined(party):
    guest = person("guest")
    join(guest, party.lunch_id)

    listed = client_for(guest).get("/api/lunches").json()

    assert [(x["id"], x["joined"]) for x in listed] == [(party.lunch_id, True)]
    assert listed[0]["org_kind"] == "personal"
    assert listed[0]["host"]["name"] == "Asha"


def test_people_cannot_open_a_lunch_before_joining_it(party):
    guest = person("guest")

    assert client_for(guest).get(f"/api/lunches/{party.lunch_id}").status_code == 404
    assert client_for(guest).get("/api/lunches").json() == []


def test_a_company_lunch_cannot_be_joined_from_outside(lunch, other_company):
    outsider = make_user(other_company, "Olga")
    gmail = person("guest")

    for who in (outsider, gmail):
        response = join(who, lunch.id)
        assert response.status_code == 403
        assert "Acme" in response.json()["detail"]
    assert not SessionMember.objects.exists()


def test_a_company_lunch_join_by_its_own_people_is_a_no_op(lunch, member):
    response = join(member, lunch.id)

    assert response.status_code == 200
    assert response.json()["joined"] is False
    assert not SessionMember.objects.exists()


def test_a_lunch_that_has_closed_cannot_be_newly_joined(party):
    OrderSession.objects.filter(id=party.lunch_id).update(status=SessionStatus.LOCKED)

    response = join(person("late"), party.lunch_id)

    assert response.status_code == 400
    assert "closed" in response.json()["detail"]


def test_someone_who_joined_can_still_open_it_after_it_closes(party):
    guest = person("guest")
    join(guest, party.lunch_id)
    OrderSession.objects.filter(id=party.lunch_id).update(status=SessionStatus.LOCKED)

    assert join(guest, party.lunch_id).status_code == 200
    assert client_for(guest).get(f"/api/lunches/{party.lunch_id}").status_code == 200


def test_joining_an_unknown_lunch_is_not_found(db):
    response = join(person("guest"), "3f2504e0-4f89-41d3-9a0c-0305e82c3301")

    assert response.status_code == 404


def test_joining_needs_sign_in(party, client):
    assert client.post(f"/api/lunches/{party.lunch_id}/join").status_code == 401


# --- what a joined person can and cannot do -----------------------------------


@pytest.fixture
def guest(party):
    user = person("guest")
    join(user, party.lunch_id)
    return user


def test_a_joined_person_picks_changes_and_drops_their_own_dishes(party, guest):
    made = pick(guest, party.lunch_id)
    item_id = made.json()["id"]
    url = f"/api/lunches/{party.lunch_id}/items/{item_id}"

    changed = client_for(guest).patch(url, {"quantity": 3}, format="json")
    board = client_for(guest).get(f"/api/lunches/{party.lunch_id}").json()
    dropped = client_for(guest).delete(url)

    assert made.status_code == 201
    assert changed.json()["quantity"] == 3
    assert board["items"][0]["user"]["name"] == "Guest"
    assert dropped.status_code == 204


def test_a_joined_person_can_replace_their_cart_complete_and_skip(party, guest):
    cart = client_for(guest).put(
        f"/api/lunches/{party.lunch_id}/picks",
        {"items": [DISH], "complete": True},
        format="json",
    )
    other = person("other")
    join(other, party.lunch_id)
    skipped = client_for(other).put(f"/api/lunches/{party.lunch_id}/skip")

    assert cart.status_code == 200
    assert [p["name"] for p in cart.json()["completed"]] == ["Guest"]
    assert skipped.status_code == 200
    assert [p["name"] for p in skipped.json()["skipped"]] == ["Other"]


def test_a_joined_person_cannot_change_someone_elses_pick(party, guest):
    theirs = pick(party.host, party.lunch_id).json()["id"]

    response = client_for(guest).patch(
        f"/api/lunches/{party.lunch_id}/items/{theirs}", {"quantity": 9}, format="json"
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "lock"),
        ("post", "unlock"),
        ("post", "cancel"),
        ("post", "confirm"),
    ],
)
def test_a_joined_person_cannot_run_the_lunch(party, guest, method, path):
    response = getattr(client_for(guest), method)(
        f"/api/lunches/{party.lunch_id}/{path}", {}, format="json"
    )

    assert response.status_code in (403, 404)
    assert OrderSession.objects.get(id=party.lunch_id).status == SessionStatus.OPEN


def test_a_joined_person_cannot_reopen_or_skip_others(party, guest):
    host = party.host

    reopen = client_for(guest).delete(
        f"/api/lunches/{party.lunch_id}/complete/{host.id}"
    )
    skip = client_for(guest).put(f"/api/lunches/{party.lunch_id}/skip/{host.id}")

    assert reopen.status_code in (403, 404)
    assert skip.status_code in (403, 404)


def test_a_joined_person_cannot_reach_the_hosts_other_lunches(party, guest):
    finished(party)  # one lunch at a time
    second = start(party.host).json()["id"]

    assert client_for(guest).get(f"/api/lunches/{second}").status_code == 404
    assert pick(guest, second).status_code == 404
    assert [x["id"] for x in client_for(guest).get("/api/lunches").json()] == [
        party.lunch_id
    ]


def test_a_joined_person_cannot_see_the_hosts_members_or_settings(party, guest):
    client = client_for(guest)

    assert client.get("/api/company/members").status_code == 403
    assert client.get("/api/company/settings").status_code == 403
    assert client.get("/api/company/invites").status_code == 403


def test_a_joined_person_cannot_touch_the_orders(party, guest):
    client = client_for(guest)

    assert client.get("/api/bot/status").status_code == 403


def test_a_host_of_their_own_party_is_only_a_member_in_someone_elses(party):
    bela = person("bela")
    start(bela)  # bela hosts a party, so has every permission in it
    join(bela, party.lunch_id)

    assert pick(bela, party.lunch_id).status_code == 201
    assert (
        client_for(bela).post(f"/api/lunches/{party.lunch_id}/lock").status_code == 404
    )
    assert (
        client_for(bela).post(f"/api/lunches/{party.lunch_id}/cancel").status_code
        == 404
    )


def test_a_joined_person_picks_stop_when_the_lunch_closes(party, guest):
    OrderSession.objects.filter(id=party.lunch_id).update(
        cutoff_at=timezone.now() - timedelta(minutes=1)
    )

    assert pick(guest, party.lunch_id).status_code == 409


def test_company_people_keep_their_member_permissions_only(lunch, member):
    # A company member can pick in their own company's lunch, but not lock it.
    assert pick(member, lunch.id).status_code == 201
    assert client_for(member).post(f"/api/lunches/{lunch.id}/lock").status_code == 403


def test_a_company_member_without_session_read_cannot_list_lunches(company):
    pia = make_user(company, "Pia", set())

    assert client_for(pia).get("/api/lunches").status_code == 403


# --- the database's own rules -------------------------------------------------


def seen_by(org_id=None, user_id=None, joining=None):
    """What a normal (non-superuser) database role sees with these settings."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'probe_party'")
        if not cursor.fetchone():
            cursor.execute("CREATE ROLE probe_party NOLOGIN")
            cursor.execute(
                "GRANT SELECT ON order_sessions, session_members, order_items"
                " TO probe_party"
            )
        cursor.execute("SET LOCAL ROLE probe_party")
        cursor.execute(
            "SELECT set_config('app.org_id', %s, true),"
            " set_config('app.user_id', %s, true),"
            " set_config('app.join_session', %s, true)",
            [str(org_id or ""), str(user_id or ""), str(joining or "")],
        )
        cursor.execute("SELECT id FROM order_sessions")
        lunches = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute("SELECT user_id FROM session_members")
        members = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute("SELECT id FROM order_items")
        items = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute("RESET ROLE")
    return lunches, members, items


def test_the_database_shows_a_joined_lunch_only_to_the_person_who_joined(party, guest):
    other = person("other")
    item = pick(party.host, party.lunch_id).json()["id"]
    lunch = party.lunch_id

    guest_sees = seen_by(None, guest.id)
    other_sees = seen_by(None, other.id)
    nobody_sees = seen_by()

    assert guest_sees == ({lunch}, {str(guest.id)}, {item})
    assert other_sees == (set(), set(), set())
    assert nobody_sees == (set(), set(), set())


def test_the_database_shows_a_party_to_its_own_org_and_no_other(party):
    org = party.host.org_id
    stranger = person("stranger")

    assert seen_by(org, party.host.id)[0] == {party.lunch_id}
    assert seen_by(stranger.org_id, stranger.id)[0] == set()


def test_the_link_lets_the_database_show_that_one_lunch_while_joining(party):
    finished(party)  # one lunch at a time
    second = start(party.host).json()["id"]

    during = seen_by(None, None, joining=party.lunch_id)

    assert during[0] == {party.lunch_id}
    assert second not in during[0]


def test_a_membership_row_is_visible_to_its_owner_and_the_hosts_org_only(party, guest):
    other = person("other")
    join(other, party.lunch_id)

    assert seen_by(None, guest.id)[1] == {str(guest.id)}
    assert seen_by(party.host.org_id, party.host.id)[1] == {
        str(guest.id),
        str(other.id),
    }
    assert seen_by(person("stranger").org_id, None)[1] == set()


def test_joining_lets_go_of_the_link_when_it_is_done(db):
    with tenancy.scoped(None):  # opens a transaction with nothing set
        with (
            tenancy.joining("3f2504e0-4f89-41d3-9a0c-0305e82c3301"),
            connection.cursor() as c,
        ):
            c.execute("SELECT current_setting('app.join_session')")
            assert c.fetchone()[0] == "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
        with connection.cursor() as c:
            c.execute("SELECT current_setting('app.join_session')")
            assert c.fetchone()[0] == ""


# --- the data migration -------------------------------------------------------


def test_people_on_the_old_shared_gmail_org_each_get_their_own_party(db):
    migration = importlib.import_module(
        "apps.lunches.migrations.0007_personal_orgs_for_public_emails"
    )
    shared = Organization.objects.create(name="Gmail", email_domain="gmail.com")
    asha = make_user(shared, "Asha")
    bela = make_user(shared, "Bela")
    lunch = OrderSession.objects.create(
        org=shared,
        host=asha,
        cutoff_at=timezone.now() + timedelta(hours=1),
        delivery_address="Home",
    )
    OrderItem.objects.create(
        session=lunch,
        user=bela,
        restaurant_name="R",
        dish_name="D",
        quantity=1,
        price_paise=100,
    )
    acme = Organization.objects.create(name="Acme", email_domain="acme.com")
    colleague = make_user(acme, "Cleo")

    from django.apps import apps

    migration.give_public_email_people_their_own_party(
        apps, SimpleNamespace(connection=connection)
    )

    asha, bela, colleague = (User.objects.get(id=u.id) for u in (asha, bela, colleague))
    assert asha.org is not None and bela.org is not None
    assert asha.org.kind == bela.org.kind == OrgKind.PERSONAL
    assert asha.org_id != bela.org_id
    assert asha.org.name == "Asha's party"
    assert set(asha.permissions) == ALL_PERMISSIONS
    lunch.refresh_from_db()
    assert lunch.org_id == asha.org_id  # it moved with its host
    assert OrderItem.objects.get(session=lunch).user_id == bela.id  # picks are kept
    assert SessionMember.objects.filter(session=lunch, user=bela).exists()
    assert colleague.org_id == acme.id  # company people are left alone
    # Bela can still open it, through the membership.
    assert client_for(bela).get(f"/api/lunches/{lunch.id}").status_code == 200


def test_the_data_migration_does_nothing_without_a_shared_public_org(company):
    from django.apps import apps

    migration = importlib.import_module(
        "apps.lunches.migrations.0007_personal_orgs_for_public_emails"
    )

    migration.give_public_email_people_their_own_party(
        apps, SimpleNamespace(connection=connection)
    )

    assert Organization.objects.count() == 1


def test_public_email_domains_can_be_listed_in_the_settings(settings):
    assert "gmail.com" in settings.PUBLIC_EMAIL_DOMAINS
    assert "acme.com" not in settings.PUBLIC_EMAIL_DOMAINS
    assert accounts.is_public_email("Someone@GMAIL.com")
    assert not accounts.is_public_email("someone@acme.com")


# --- one party at a time, and leaving one -------------------------------------


def leave(user, lunch_id):
    return client_for(user).delete(f"/api/lunches/{lunch_id}/join")


def test_a_host_cannot_start_another_lunch_while_theirs_is_running(party):
    response = start(party.host)

    assert response.status_code == 409
    assert "Finish it first" in response.json()["detail"]


def test_a_guest_cannot_start_their_own_party_while_in_a_running_one(party, guest):
    response = start(guest)

    assert response.status_code == 409
    assert "Leave it first" in response.json()["detail"]


def test_a_guest_who_left_can_start_their_own_party(party, guest):
    assert leave(guest, party.lunch_id).status_code == 204

    assert start(guest).status_code == 201


def test_arrived_food_still_needs_the_last_step_before_a_new_party(party, guest):
    arrived(party)

    response = start(party.host)

    assert response.status_code == 409
    assert "Finish it first" in response.json()["detail"]


def test_once_the_host_finishes_the_lunch_a_new_party_can_start(party, guest):
    arrived(party)
    close = client_for(party.host).post(f"/api/lunches/{party.lunch_id}/close")
    assert close.status_code == 200

    assert start(party.host).status_code == 201
    assert start(guest).status_code == 201


def test_a_called_off_lunch_frees_the_host(party):
    OrderSession.objects.filter(id=party.lunch_id).update(
        status=SessionStatus.CANCELLED
    )

    assert start(party.host).status_code == 201


def test_a_guest_can_leave_an_arrived_lunch_and_start_their_own(party, guest):
    arrived(party)

    assert start(guest).status_code == 409
    assert leave(guest, party.lunch_id).status_code == 204
    assert start(guest).status_code == 201


def test_leaving_drops_the_guests_picks_and_the_lunch_from_their_list(party, guest):
    pick(guest, party.lunch_id)

    assert leave(guest, party.lunch_id).status_code == 204
    assert not OrderItem.objects.filter(session_id=party.lunch_id).exists()
    assert not SessionMember.objects.filter(session_id=party.lunch_id).exists()
    assert client_for(guest).get("/api/lunches").json() == []
    assert client_for(guest).get(f"/api/lunches/{party.lunch_id}").status_code == 404


def test_a_guest_with_food_on_order_stays_until_it_arrives(party, guest):
    pick(guest, party.lunch_id)
    OrderSession.objects.filter(id=party.lunch_id).update(status=SessionStatus.PLACED)

    response = leave(guest, party.lunch_id)

    assert response.status_code == 409
    assert SessionMember.objects.filter(session_id=party.lunch_id).exists()


def test_a_guest_with_nothing_on_order_can_leave_a_locked_lunch(party, guest):
    OrderSession.objects.filter(id=party.lunch_id).update(status=SessionStatus.LOCKED)

    assert leave(guest, party.lunch_id).status_code == 204


def test_the_host_cannot_leave_their_own_lunch(party):
    response = leave(party.host, party.lunch_id)

    assert response.status_code == 409
    assert "Close it instead" in response.json()["detail"]


def test_leaving_a_lunch_you_are_not_in_is_not_found(party):
    assert leave(person("stranger"), party.lunch_id).status_code == 404


# --- who is in the party, as everyone in it sees it ---------------------------


def people_on(user, lunch_id):
    board = client_for(user).get(f"/api/lunches/{lunch_id}").json()
    return [(p["name"], p["email"]) for p in board["people"]]


def test_host_and_guests_see_the_same_people_with_emails(party, guest):
    other = person("other")
    join(other, party.lunch_id)
    expected = [
        ("Asha", "asha@gmail.com"),
        ("Guest", "guest@gmail.com"),
        ("Other", "other@gmail.com"),
    ]

    assert people_on(party.host, party.lunch_id) == expected
    assert people_on(guest, party.lunch_id) == expected
    assert people_on(other, party.lunch_id) == expected


def test_someone_who_joined_shows_before_they_pick(party, guest):
    names = [
        p["name"]
        for p in client_for(party.host)
        .get(f"/api/lunches/{party.lunch_id}")
        .json()["people"]
    ]

    assert names == ["Asha", "Guest"]
    assert not OrderItem.objects.filter(session_id=party.lunch_id).exists()


def test_someone_who_left_is_no_longer_in_the_people(party, guest):
    client_for(guest).delete(f"/api/lunches/{party.lunch_id}/join")

    assert [
        p["name"]
        for p in client_for(party.host)
        .get(f"/api/lunches/{party.lunch_id}")
        .json()["people"]
    ] == ["Asha"]


def test_reading_the_people_leaves_the_callers_scope_as_it_was(party, guest):
    client_for(guest).get(f"/api/lunches/{party.lunch_id}")

    assert [x["id"] for x in client_for(guest).get("/api/lunches").json()] == [
        party.lunch_id
    ]


# --- the restaurants a lunch offers -------------------------------------------

OFFER = [
    {
        "id": "12345",
        "name": "Thaal Kitchen",
        "cuisines": ["Kerala"],
        "rating": 4.3,
        "eta": "25-30 mins",
        "distance_km": 1.9,
        "photo": "https://img.example/t.jpg",
    },
    {"id": "67890", "name": "Chicking"},
]


def test_everyone_in_the_party_sees_the_restaurants_the_host_offered(db):
    host = person("hosta")
    lunch_id = start(host, offered_restaurants=OFFER).json()["id"]
    guest = person("guesta")
    join(guest, lunch_id)

    for who in (host, guest):
        offer = (
            client_for(who)
            .get(f"/api/lunches/{lunch_id}")
            .json()["offered_restaurants"]
        )
        assert [r["name"] for r in offer] == ["Thaal Kitchen", "Chicking"]
        assert offer[0]["distance_km"] == 1.9


def test_a_lunch_without_an_offer_lists_none(party):
    assert party.board["offered_restaurants"] == []


def test_an_offered_restaurant_needs_an_id_and_a_name(db):
    response = start(person("hostb"), offered_restaurants=[{"name": "No id"}])

    assert response.status_code == 400


# --- closing a lunch ----------------------------------------------------------


def test_the_host_closes_an_arrived_lunch_and_everyone_sees_it_closed(party, guest):
    arrived(party)

    response = client_for(party.host).post(f"/api/lunches/{party.lunch_id}/close")
    seen = client_for(guest).get("/api/lunches").json()[0]

    assert response.status_code == 200
    assert response.json()["closed_at"] is not None
    assert seen["closed_at"] == response.json()["closed_at"]


def test_a_lunch_closes_only_once_its_food_has_arrived(party):
    response = client_for(party.host).post(f"/api/lunches/{party.lunch_id}/close")

    assert response.status_code == 409


def test_a_guest_cannot_close_the_hosts_lunch(party, guest):
    arrived(party)

    assert client_for(guest).post(
        f"/api/lunches/{party.lunch_id}/close"
    ).status_code in (403, 404)


def test_an_offered_photo_with_spaces_is_kept_encoded(db):
    photo = "https://media-assets.swiggy.com/x/1025860 (1).jpg"
    offer = [{"id": "1", "name": "The samco", "photo": photo}]

    response = start(person("hostc"), offered_restaurants=offer)

    assert response.status_code == 201
    saved = response.json()["offered_restaurants"][0]["photo"]
    assert saved == "https://media-assets.swiggy.com/x/1025860%20(1).jpg"


def test_an_unusable_photo_is_dropped_not_refused(db):
    offer = [{"id": "1", "name": "Somewhere", "photo": "not a link"}]

    response = start(person("hostd"), offered_restaurants=offer)

    assert response.status_code == 201
    assert response.json()["offered_restaurants"][0]["photo"] == ""


# --- the live board -------------------------------------------------------------


def test_a_guest_joining_writes_the_live_board_with_who_may_read_it(
    party, settings, monkeypatch, django_capture_on_commit_callbacks
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
    guest = person("guest")

    with django_capture_on_commit_callbacks(execute=True):
        assert join(guest, party.lunch_id).status_code == 200

    board = written[party.lunch_id]
    assert board["member_uids"] == sorted([party.host.firebase_uid, guest.firebase_uid])
    assert guest.email in [p["email"] for p in board["people"]]
