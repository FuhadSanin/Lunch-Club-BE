"""Background jobs.

In production each job is a Google Cloud Task that posts its payload to
/internal/tasks/<name> at the scheduled time, retrying with backoff on failure.
With CLOUD_TASKS_ENABLED off (local dev, tests) due jobs run inline instead and
future-dated ones are skipped.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.errors import RetryLater

logger = logging.getLogger(__name__)

Handler = Callable[[dict], None]
registry: dict[str, Handler] = {}


def job(name: str) -> Callable[[Handler], Handler]:
    """Register a function as the handler for job `name`."""

    def register(handler: Handler) -> Handler:
        registry[name] = handler
        return handler

    return register


def enqueue(
    name: str, payload: dict, *, at: datetime | None = None, key: str = ""
) -> None:
    """Queue a job once the current transaction commits.

    Jobs with the same name and `key` are created only once, so retrying the code
    that enqueues them is safe.
    """
    transaction.on_commit(lambda: _dispatch(name, payload, at, key))


def _dispatch(name: str, payload: dict, at: datetime | None, key: str) -> None:
    if settings.CLOUD_TASKS_ENABLED:
        _create_cloud_task(name, payload, at, key)
    elif at is None or at <= timezone.now():
        try:
            registry[name](payload)
        except RetryLater:
            logger.warning("Inline job asked to retry", extra={"fields": {"job": name}})
    else:
        logger.info(
            "Scheduled job skipped in local mode", extra={"fields": {"job": name}}
        )


@cache
def _client():
    from google.cloud import tasks_v2

    return tasks_v2.CloudTasksClient()


def _create_cloud_task(name: str, payload: dict, at: datetime | None, key: str) -> None:
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2
    from google.protobuf import timestamp_pb2

    client = _client()
    location = (settings.GCP_PROJECT, settings.GCP_REGION, settings.CLOUD_TASKS_QUEUE)
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{settings.TASKS_BASE_URL}/internal/tasks/{name}",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(payload).encode(),
            "oidc_token": {
                "service_account_email": settings.TASKS_SERVICE_ACCOUNT,
                "audience": settings.TASKS_BASE_URL,
            },
        }
    }
    if key:
        task["name"] = client.task_path(*location, f"{name}-{key}")
    if at:
        schedule = timestamp_pb2.Timestamp()
        schedule.FromDatetime(at.astimezone(UTC).replace(tzinfo=None))
        task["schedule_time"] = schedule
    try:
        client.create_task(parent=client.queue_path(*location), task=task)
    except AlreadyExists:
        logger.info("Job already queued", extra={"fields": {"job": name, "key": key}})
