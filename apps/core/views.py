from functools import cache

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import jobs
from apps.core.errors import RetryLater


class TaskRunner:
    """The caller of an internal job endpoint: Cloud Tasks, never a person."""

    is_authenticated = True


@cache
def _google_request():
    from google.auth.transport.requests import Request

    return Request()


class CloudTasksAuthentication(BaseAuthentication):
    """Accepts only OIDC tokens that Cloud Tasks signs with our task service account."""

    def authenticate(self, request):
        from google.oauth2 import id_token

        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme != "Bearer" or not token:
            raise AuthenticationFailed("Missing task token.")
        try:
            claims = id_token.verify_oauth2_token(
                token, _google_request(), audience=settings.TASKS_BASE_URL
            )
        except ValueError as error:
            raise AuthenticationFailed("Invalid task token.") from error
        if claims.get("email") != settings.TASKS_SERVICE_ACCOUNT or not claims.get(
            "email_verified"
        ):
            raise AuthenticationFailed(
                "Task token is not from the task service account."
            )
        return TaskRunner(), claims


class RunJobView(APIView):
    """POST /internal/tasks/<name>: Cloud Tasks delivers a queued job here."""

    authentication_classes = [CloudTasksAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, name: str):
        handler = jobs.registry.get(name)
        if handler is None:
            raise NotFound(f"Unknown job {name}.")
        try:
            handler(request.data)
        except RetryLater as reason:
            # Any non-2xx makes Cloud Tasks retry the job with backoff.
            return Response({"detail": str(reason)}, status=503)
        return Response(status=204)
