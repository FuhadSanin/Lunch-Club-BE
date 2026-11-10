from django.db import transaction
from django.urls import path

from apps.core.views import RunJobView

urlpatterns = [
    # Jobs manage their own transactions (see apps.core.tenancy.scoped), so that a
    # half-finished Swiggy order is committed as "placing" before the slow step starts.
    path("tasks/<slug:name>", transaction.non_atomic_requests(RunJobView.as_view())),
]
