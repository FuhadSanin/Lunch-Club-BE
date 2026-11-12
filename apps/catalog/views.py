from django.db import transaction
from django.http import Http404
from django.utils.decorators import method_decorator
from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Permission
from apps.accounts.services import is_public_email
from apps.catalog import places, services
from apps.catalog.places import PlacesError
from apps.catalog.swiggy import SwiggyError


class SwiggyUnavailable(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "swiggy_unavailable"
    default_detail = "Swiggy isn't answering right now. Try again in a minute."


class PlacesUnavailable(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "places_unavailable"
    default_detail = "Place search isn't answering right now. Try again in a minute."


class CanBrowse(BasePermission):
    """People who pick food can browse: anyone in a company with the permission, and
    anyone with a public email (they host or join personal parties, with no company)."""

    message = "You need the restaurant_read permission to do this."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if user.org_id is None:
            return is_public_email(user.email)
        return user.has_perm(Permission.RESTAURANT_READ)


class PlaceSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)


class SearchSerializer(PlaceSerializer):
    q = serializers.CharField(
        max_length=80, trim_whitespace=True, required=False, default=""
    )


class DishSearchSerializer(PlaceSerializer):
    q = serializers.CharField(min_length=2, max_length=80, trim_whitespace=True)


def parse(serializer_class, request) -> dict:
    serializer = serializer_class(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


# No company data is read, so these skip the request transaction rather than hold a
# database connection open while Swiggy answers.
@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CatalogAPIView(APIView):
    permission_classes = [IsAuthenticated, CanBrowse]
    required_permissions = {"GET": Permission.RESTAURANT_READ}

    def handle_exception(self, exc):
        if isinstance(exc, SwiggyError):
            exc = SwiggyUnavailable()
        elif isinstance(exc, PlacesError):
            exc = PlacesUnavailable()
        return super().handle_exception(exc)


class RestaurantsView(CatalogAPIView):
    """GET /api/restaurants?lat=&lng=[&q=]: search Swiggy, or popular places nearby."""

    def get(self, request):
        query = parse(SearchSerializer, request)
        return Response(
            {
                "restaurants": services.restaurants(
                    query["lat"], query["lng"], query["q"]
                )
            }
        )


class MenuView(CatalogAPIView):
    """GET /api/restaurants/<id>/menu?lat=&lng=: a restaurant's details and menu."""

    def get(self, request, restaurant_id):
        place = parse(PlaceSerializer, request)
        found = services.menu(str(restaurant_id), **place)
        if found is None:
            raise Http404("Swiggy doesn't list this restaurant for this location.")
        return Response(found)


class DishesView(CatalogAPIView):
    """GET /api/dishes?lat=&lng=&q=: dishes by name, each with its restaurant."""

    def get(self, request):
        query = parse(DishSearchSerializer, request)
        return Response(
            {"dishes": services.dishes(query["lat"], query["lng"], query["q"])}
        )


class PlaceSearchSerializer(serializers.Serializer):
    q = serializers.CharField(min_length=2, max_length=80, trim_whitespace=True)
    lat = serializers.FloatField(min_value=-90, max_value=90, required=False)
    lng = serializers.FloatField(min_value=-180, max_value=180, required=False)


class PlacesView(CatalogAPIView):
    """GET /api/places?q=: places matching what is being typed, to pick an address."""

    def get(self, request):
        query = parse(PlaceSearchSerializer, request)
        return Response(places.suggest(query["q"], query.get("lat"), query.get("lng")))


class PlaceView(CatalogAPIView):
    """GET /api/places/<place_id>: the address and coordinates of a suggested place."""

    def get(self, request, place_id):
        found = places.detail(place_id)
        if found is None:
            raise Http404("No such place.")
        return Response(found)
