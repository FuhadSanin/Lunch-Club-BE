from functools import cache

import firebase_admin
from django.conf import settings


@cache
def app() -> firebase_admin.App:
    """The Firebase app, created on first use with the runtime's default credentials."""
    options = {"projectId": settings.GCP_PROJECT} if settings.GCP_PROJECT else None
    return firebase_admin.initialize_app(options=options)
