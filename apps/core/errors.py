import math
import re

from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, ErrorDetail, NotFound, Throttled


class Conflict(APIException):
    """The request is valid but the lunch is not in a state that allows it."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"
    default_detail = "That can't be done right now."


class RetryLater(Exception):
    """Raised by a background job to make Cloud Tasks retry it with backoff."""


# What Django says when a lookup finds nothing, like
# "No OrderSession matches the given query."
_NOT_IN_DATABASE = re.compile(r"^No .+ matches the given query\.$")
NOT_FOUND = (
    "We couldn't find that. It may have been removed, or you may not have access to it."
)


def exception_handler(exc, context):
    """Every error the API answers with is a sentence a person can act on.

    Two of the framework's own aren't: a lookup that finds nothing names the database
    table, and a rate limit talks about throttling. Everything else already carries a
    message written for the person (see the services), and passes through unchanged.
    """
    # Imported here: rest_framework.views loads the settings, whose sign-in classes
    # import this module.
    from rest_framework.views import exception_handler as drf_exception_handler

    if isinstance(exc, Http404) and (
        not exc.args or _NOT_IN_DATABASE.match(str(exc.args[0]))
    ):
        exc = NotFound(NOT_FOUND)
    elif isinstance(exc, Throttled):
        wait = getattr(exc, "wait", None)  # set by the framework; not in its type stubs
        seconds = math.ceil(wait) if wait else None
        when = (
            f"in {seconds} second{'' if seconds == 1 else 's'}"
            if seconds
            else "in a moment"
        )
        # Only the words change: the wait stays, for the Retry-After header.
        exc.detail = ErrorDetail(
            f"That's a lot of requests at once. Try again {when}.", code="throttled"
        )
    return drf_exception_handler(exc, context)
