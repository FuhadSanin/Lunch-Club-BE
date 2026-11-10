from django.urls import path

from apps.ordering import views

urlpatterns = [
    path("bot/status", views.BotStatusView.as_view()),
    path("lunches/<uuid:lunch_id>/confirm", views.ConfirmView.as_view()),
    path("orders/<uuid:order_id>/retry", views.OrderRetryView.as_view()),
    path("orders/<uuid:order_id>/placed", views.OrderPlacedView.as_view()),
    path("orders/<uuid:order_id>/delivered", views.OrderDeliveredView.as_view()),
]
