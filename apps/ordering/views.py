from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response

from apps.accounts.models import Permission
from apps.accounts.permissions import CompanyAPIView
from apps.lunches.models import SwiggyOrder
from apps.lunches.views import board_response, get_lunch_for_update
from apps.ordering import bot_status, services


class SwiggyOrderNumberSerializer(serializers.Serializer):
    # Optional: the host may just mark the order as placed.
    swiggy_order_id = serializers.CharField(
        max_length=64,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
        default="",
    )


def lock_company_order(request, order_id) -> SwiggyOrder:
    order = services.lock_order(order_id)
    if order is None or order.session.org_id != request.user.org_id:
        raise Http404
    return order


class BotStatusView(CompanyAPIView):
    """GET /api/bot/status: is the order bot ready, and how to fix what isn't."""

    required_permissions = {"GET": Permission.ORDER_UPDATE}

    def get(self, request):
        checks = bot_status.checks()
        return Response(
            {
                "ready": all(item["ok"] for item in checks),
                "checks": checks,
                "commands": bot_status.commands(),
            }
        )


class ConfirmView(CompanyAPIView):
    """POST /api/lunches/<id>/confirm: approve the reviewed cart; orders go out."""

    required_permissions = {"POST": Permission.REVIEW_CREATE}

    def post(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        services.confirm(lunch)
        return board_response(lunch.id)


class OrderRetryView(CompanyAPIView):
    """POST /api/orders/<id>/retry: have the bot try an order that needs the host."""

    required_permissions = {"POST": Permission.ORDER_UPDATE}

    def post(self, request, order_id):
        order = lock_company_order(request, order_id)
        services.retry(order)
        return board_response(order.session_id)


class OrderPlacedView(CompanyAPIView):
    """POST /api/orders/<id>/placed: enter the number of an order placed in Swiggy."""

    required_permissions = {"POST": Permission.ORDER_UPDATE}

    def post(self, request, order_id):
        serializer = SwiggyOrderNumberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = lock_company_order(request, order_id)
        services.record_placed(order, serializer.validated_data["swiggy_order_id"])
        return board_response(order.session_id)


class OrderDeliveredView(CompanyAPIView):
    """POST /api/orders/<id>/delivered: the food has arrived."""

    required_permissions = {"POST": Permission.ORDER_UPDATE}

    def post(self, request, order_id):
        order = lock_company_order(request, order_id)
        services.record_delivered(order)
        return board_response(order.session_id)
