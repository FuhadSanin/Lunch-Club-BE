from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"

    def ready(self):
        from apps.core import checks, rls  # noqa: F401  (registers the checks)

        rls.install()
