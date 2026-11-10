from django.apps import AppConfig


class LunchesConfig(AppConfig):
    name = "apps.lunches"

    def ready(self):
        from apps.lunches import jobs  # noqa: F401  registers the job handlers
