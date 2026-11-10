from rest_framework import status
from rest_framework.exceptions import APIException


class Conflict(APIException):
    """The request is valid but the lunch is not in a state that allows it."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"
    default_detail = "This action is not allowed right now."


class RetryLater(Exception):
    """Raised by a background job to make Cloud Tasks retry it with backoff."""
