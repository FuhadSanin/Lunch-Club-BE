from functools import cache

from django.conf import settings
from django.utils.module_loading import import_string

from apps.ordering.providers.base import OrderProvider


@cache
def get_provider() -> OrderProvider:
    return import_string(settings.ORDER_PROVIDER)()
