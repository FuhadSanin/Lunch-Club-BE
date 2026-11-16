"""System checks that run with `manage.py check --deploy` (`make check`)."""

from django.conf import settings
from django.core.checks import Tags, Warning, register  # noqa: A004


@register(Tags.security, deploy=True)
def no_wildcard_hosts(app_configs, **kwargs):
    """ALLOWED_HOSTS must name the exact hosts. A leading dot (".vercel.app") or "*"
    accepts every host under that name, including other people's apps on the same
    platform, which defeats what the setting is for."""
    if settings.DEBUG:
        return []
    wide = [h for h in settings.ALLOWED_HOSTS if h.startswith(".") or "*" in h]
    if not wide:
        return []
    return [
        Warning(
            f"DJANGO_ALLOWED_HOSTS accepts whole families of hosts: {', '.join(wide)}.",
            hint="List the exact host names, like lunchclubbe.vercel.app.",
            id="lunch.W001",
        )
    ]
