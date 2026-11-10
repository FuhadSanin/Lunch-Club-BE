from django.db import transaction
from django.http import Http404
from django.utils.decorators import method_decorator
from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Permission
from apps.accounts.permissions import HasCompany, HasRequiredPermission
from apps.catalog import services
from apps.catalog.swiggy import SwiggyError


class SwiggyUnavailable(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "swiggy_unavailable"
    default_detail = "Swiggy isn't answering right now. Try again in a minute."


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
    permission_classes = [IsAuthenticated, HasCompany, HasRequiredPermission]
    required_permissions = {"GET": Permission.RESTAURANT_READ}

    def handle_exception(self, exc):
        if isinstance(exc, SwiggyError):
            exc = SwiggyUnavailable()
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
