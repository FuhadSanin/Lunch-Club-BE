"""Push notifications (Firebase Cloud Messaging) and email.

Each company has one FCM topic; the web app subscribes a device to it after sign-in.
"""

import logging
from uuid import UUID

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _topic(org_id: UUID) -> str:
    return f"org-{org_id}"


def subscribe_to_company(device_token: str, org_id: UUID) -> None:
    if not settings.PUSH_ENABLED:
        return
    from firebase_admin import messaging

    from apps.core import firebase

    messaging.subscribe_to_topic([device_token], _topic(org_id), app=firebase.app())


def push_to_company(org_id: UUID, title: str, body: str, link: str = "") -> None:
    if not settings.PUSH_ENABLED:
        logger.info(
            "Push skipped", extra={"fields": {"title": title, "org_id": org_id}}
        )
        return
    from firebase_admin import messaging

    from apps.core import firebase

    message = messaging.Message(
        topic=_topic(org_id),
        notification=messaging.Notification(title=title, body=body),
        webpush=messaging.WebpushConfig(
            fcm_options=messaging.WebpushFCMOptions(link=link) if link else None
        ),
    )
    messaging.send(message, app=firebase.app())


def email(recipients: list[str], subject: str, body: str) -> None:
    if recipients:
        send_mail(subject, body, None, recipients)
