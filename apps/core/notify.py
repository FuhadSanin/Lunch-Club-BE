"""Push notifications (Firebase Cloud Messaging) and email.

A company has one FCM topic, and so does each personal party's lunch (its guests aren't
in the company). The web app turns notifications on after sign-in (`POST
/api/me/push-token`); a device is then subscribed to the topics it should hear.
"""

import logging
from collections.abc import Iterable
from uuid import UUID

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _topic(org_id: UUID) -> str:
    return f"org-{org_id}"


def _lunch_topic(lunch_id: UUID) -> str:
    return f"lunch-{lunch_id}"


def _subscribe(tokens: Iterable[str], topic: str) -> None:
    tokens = list(tokens)
    if not settings.PUSH_ENABLED or not tokens:
        return
    from firebase_admin import messaging

    from apps.core import firebase

    messaging.subscribe_to_topic(tokens, topic, app=firebase.app())


def _unsubscribe(tokens: Iterable[str], topic: str) -> None:
    tokens = list(tokens)
    if not settings.PUSH_ENABLED or not tokens:
        return
    from firebase_admin import messaging

    from apps.core import firebase

    messaging.unsubscribe_from_topic(tokens, topic, app=firebase.app())


def _push(topic: str, title: str, body: str, link: str) -> None:
    if not settings.PUSH_ENABLED:
        logger.info("Push skipped", extra={"fields": {"title": title, "topic": topic}})
        return
    from firebase_admin import messaging

    from apps.core import firebase

    message = messaging.Message(
        topic=topic,
        notification=messaging.Notification(title=title, body=body),
        webpush=messaging.WebpushConfig(
            fcm_options=messaging.WebpushFCMOptions(link=link) if link else None
        ),
    )
    messaging.send(message, app=firebase.app())


def push_to_devices(
    tokens: Iterable[str], title: str, body: str, link: str = ""
) -> None:
    """Push to one person's own devices (their tokens), not a topic."""
    tokens = list(tokens)
    if not settings.PUSH_ENABLED or not tokens:
        logger.info("Push skipped", extra={"fields": {"title": title}})
        return
    from firebase_admin import messaging

    from apps.core import firebase

    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body),
        webpush=messaging.WebpushConfig(
            fcm_options=messaging.WebpushFCMOptions(link=link) if link else None
        ),
    )
    messaging.send_each_for_multicast(message, app=firebase.app())


def subscribe_to_company(device_token: str, org_id: UUID) -> None:
    _subscribe([device_token], _topic(org_id))


def unsubscribe_from_company(device_tokens: Iterable[str], org_id: UUID) -> None:
    _unsubscribe(device_tokens, _topic(org_id))


def push_to_company(org_id: UUID, title: str, body: str, link: str = "") -> None:
    _push(_topic(org_id), title, body, link)


def subscribe_to_lunch(device_tokens: Iterable[str], lunch_id: UUID) -> None:
    _subscribe(device_tokens, _lunch_topic(lunch_id))


def unsubscribe_from_lunch(device_tokens: Iterable[str], lunch_id: UUID) -> None:
    _unsubscribe(device_tokens, _lunch_topic(lunch_id))


def push_to_lunch(lunch_id: UUID, title: str, body: str, link: str = "") -> None:
    _push(_lunch_topic(lunch_id), title, body, link)


def email(recipients: list[str], subject: str, body: str) -> None:
    if recipients:
        send_mail(subject, body, None, recipients)
