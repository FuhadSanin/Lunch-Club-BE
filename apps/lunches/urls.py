from django.urls import path

from apps.lunches import views

urlpatterns = [
    path("lunches", views.LunchesView.as_view()),
    path("lunches/<uuid:lunch_id>", views.LunchView.as_view()),
    path("lunches/<uuid:lunch_id>/items", views.ItemsView.as_view()),
    path("lunches/<uuid:lunch_id>/items/<uuid:item_id>", views.ItemView.as_view()),
    path("lunches/<uuid:lunch_id>/picks", views.PicksView.as_view()),
    path("lunches/<uuid:lunch_id>/skip", views.SkipView.as_view()),
    path("lunches/<uuid:lunch_id>/skip/<uuid:user_id>", views.SkipPersonView.as_view()),
    path("lunches/<uuid:lunch_id>/complete", views.CompleteView.as_view()),
    path("lunches/<uuid:lunch_id>/complete/<uuid:user_id>", views.ReopenView.as_view()),
    path("lunches/<uuid:lunch_id>/lock", views.LockView.as_view()),
    path("lunches/<uuid:lunch_id>/unlock", views.UnlockView.as_view()),
    path("lunches/<uuid:lunch_id>/cancel", views.CancelView.as_view()),
]
