import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only")
# Tests check real sign-in, even when a developer's .env turns on the local shortcut.
os.environ["DEV_AUTH_EMAIL"] = ""

from config.settings import *  # noqa: E402, F403

# Tests call the API over plain HTTP.
SECURE_SSL_REDIRECT = False
