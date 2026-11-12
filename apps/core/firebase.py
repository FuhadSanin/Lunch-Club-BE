import json
from functools import cache

import firebase_admin
from django.conf import settings
from firebase_admin import credentials


@cache
def app() -> firebase_admin.App:
    """The Firebase app, created on first use.

    Where there is no runtime service account (Vercel, a laptop), a service-account key
    in FIREBASE_CREDENTIALS_JSON is used; otherwise the runtime's default credentials.
    """
    options = {"projectId": settings.GCP_PROJECT} if settings.GCP_PROJECT else None
    if settings.FIREBASE_CREDENTIALS_JSON:
        key = credentials.Certificate(json.loads(settings.FIREBASE_CREDENTIALS_JSON))
        return firebase_admin.initialize_app(key, options=options)
    return firebase_admin.initialize_app(options=options)
