from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import rbac
from apps.accounts.models import (
    ALL_PERMISSIONS,
    MEMBER_PERMISSIONS,
    Address,
    Organization,
    Permission,
    User,
)
from apps.lunches.models import OrderSession
from apps.ordering.providers import get_provider


@pytest.fixture
def company(db):
    return Organization.objects.create(name="Acme", email_domain="acme.com")


@pytest.fixture
def other_company(db):
    return Organization.objects.create(name="Globex", email_domain="globex.com")


HOST_PERMISSIONS = MEMBER_PERMISSIONS | {
    Permission.SESSION_CREATE,
    Permission.SESSION_UPDATE,
    Permission.SESSION_DELETE,
    Permission.REVIEW_CREATE,
    Permission.ORDER_UPDATE,
}


def make_user(org, name, permissions=MEMBER_PERMISSIONS):
    """A person in `org`, with a role that grants exactly `permissions`."""
    domain = org.email_domain if org else "gmail.com"
    user = User.objects.create(
        org=org,
        name=name,
        email=f"{name.lower()}@{domain}",
        firebase_uid=f"uid-{name}",
    )
    if org and permissions:
        rbac.give_role(user, rbac.role_by_permissions(org, permissions))
    return user


@pytest.fixture
def admin(company):
    return make_user(company, "Asha", ALL_PERMISSIONS)


@pytest.fixture
def host(company):
    return make_user(company, "Hari", HOST_PERMISSIONS)


@pytest.fixture
def member(company):
    return make_user(company, "Meera")


def client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def lunch(host):
    return OrderSession.objects.create(
        org=host.org,
        host=host,
        cutoff_at=timezone.now() + timedelta(hours=1),
        address=Address.objects.create(
            org=host.org, address="Acme office, 4th floor", saved=False
        ),
    )


@pytest.fixture
def fake_provider(settings):
    settings.ORDER_PROVIDER = "tests.fakes.FakeProvider"
    get_provider.cache_clear()
    yield get_provider()
    get_provider.cache_clear()
