from django.urls import path

from apps.catalog import views

urlpatterns = [
    path("restaurants", views.RestaurantsView.as_view()),
    path("restaurants/<int:restaurant_id>/menu", views.MenuView.as_view()),
    path("dishes", views.DishesView.as_view()),
]
