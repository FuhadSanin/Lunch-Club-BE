from django.http import JsonResponse
from django.urls import include, path


def healthz(_request):
    return JsonResponse({"ok": True})


urlpatterns = [
    path("healthz", healthz),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.lunches.urls")),
    path("api/", include("apps.ordering.urls")),
    path("api/", include("apps.catalog.urls")),
    path("internal/", include("apps.core.urls")),
]
