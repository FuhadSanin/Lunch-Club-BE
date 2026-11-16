from django.urls import path

from apps.lunches import views

urlpatterns = [
    path("local-menus", views.LocalMenusView.as_view()),
    path("local-menus/<uuid:menu_id>", views.LocalMenuView.as_view()),
    path("lunches", views.LunchesView.as_view()),
    path("lunches/<uuid:lunch_id>", views.LunchView.as_view()),
    path("lunches/<uuid:lunch_id>/history", views.HistoryView.as_view()),
    path("lunches/<uuid:lunch_id>/join", views.JoinView.as_view()),
    path("lunches/<uuid:lunch_id>/items", views.ItemsView.as_view()),
    path("lunches/<uuid:lunch_id>/items/<uuid:item_id>", views.ItemView.as_view()),
    path("lunches/<uuid:lunch_id>/picks", views.PicksView.as_view()),
    path("lunches/<uuid:lunch_id>/skip", views.SkipView.as_view()),
    path("lunches/<uuid:lunch_id>/skip/<uuid:user_id>", views.SkipPersonView.as_view()),
    path("lunches/<uuid:lunch_id>/complete", views.CompleteView.as_view()),
    path("lunches/<uuid:lunch_id>/complete/<uuid:user_id>", views.ReopenView.as_view()),
    path("lunches/<uuid:lunch_id>/joining", views.JoiningView.as_view()),
    path("lunches/<uuid:lunch_id>/ticks", views.TicksView.as_view()),
    path("lunches/<uuid:lunch_id>/lock", views.LockView.as_view()),
    path("lunches/<uuid:lunch_id>/unlock", views.UnlockView.as_view()),
    path("lunches/<uuid:lunch_id>/cancel", views.CancelView.as_view()),
    path("lunches/<uuid:lunch_id>/close", views.CloseView.as_view()),
]
