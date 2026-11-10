from django.apps import AppConfig


class OrderingConfig(AppConfig):
    name = "apps.ordering"

    def ready(self):
        from apps.ordering import jobs  # noqa: F401  registers the job handlers
