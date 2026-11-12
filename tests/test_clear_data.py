import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import Organization, User
from apps.lunches.models import OrderSession

# TRUNCATE can't run inside the transaction the other tests use, so these commit.
pytestmark = pytest.mark.django_db(transaction=True)


def test_without_yes_it_deletes_nothing(lunch, settings):
    settings.DEBUG = True

    with pytest.raises(CommandError, match="--yes"):
        call_command("clear_data")

    assert OrderSession.objects.count() == 1


def test_yes_empties_every_table(lunch, settings):
    settings.DEBUG = True

    call_command("clear_data", "--yes")

    assert OrderSession.objects.count() == 0
    assert User.objects.count() == 0
    assert Organization.objects.count() == 0


def test_a_deployed_database_needs_an_extra_flag(lunch, settings):
    settings.DEBUG = False

    with pytest.raises(CommandError, match="--allow-production"):
        call_command("clear_data", "--yes")

    assert OrderSession.objects.count() == 1
    call_command("clear_data", "--yes", "--allow-production")
    assert OrderSession.objects.count() == 0
