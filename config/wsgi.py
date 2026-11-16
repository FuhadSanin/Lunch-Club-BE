import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

# /mcp (the AI assistant server) is served by the same app, when it can be: see
# config/mcp_mount.py. Importing settings only after Django is set up.
from django.conf import settings  # noqa: E402

from config.mcp_mount import mount_mcp  # noqa: E402

application = mount_mcp(application, settings.API_PUBLIC_URL, settings.APP_URL)
