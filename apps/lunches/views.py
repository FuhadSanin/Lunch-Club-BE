from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from apps.accounts.models import Permission, User
from apps.accounts.permissions import CompanyAPIView
from apps.lunches import services
from apps.lunches.models import OrderSession
from apps.lunches.queries import with_board
from apps.lunches.serializers import (
    ItemSerializer,
    LunchDetailSerializer,
    LunchSerializer,
    PicksSerializer,
    StartLunchSerializer,
    UnlockSerializer,
)

RECENT_LUNCHES = 30


def get_lunch_for_update(request, lunch_id) -> OrderSession:
    """Fetch the caller's company lunch and lock its row until the request finishes."""
    return get_object_or_404(
        OrderSession.objects.select_for_update(of=("self",)).select_related("host"),
        id=lunch_id,
        org_id=request.user.org_id,
    )


def board_response(lunch_id, status_code=status.HTTP_200_OK) -> Response:
    lunch = with_board(OrderSession.objects.filter(id=lunch_id)).get()
    return Response(LunchDetailSerializer(lunch).data, status=status_code)


class LunchesView(CompanyAPIView):
    """GET /api/lunches: recent lunches. POST: start a lunch."""

    required_permissions = {
        "GET": Permission.SESSION_READ,
        "POST": Permission.SESSION_CREATE,
    }

    def get(self, request):
        lunches = (
            OrderSession.objects.filter(org_id=request.user.org_id)
            .select_related("host")
            .order_by("-created_at")[:RECENT_LUNCHES]
        )
        return Response(LunchSerializer(lunches, many=True).data)

    def post(self, request):
        serializer = StartLunchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lunch = services.start(request.user, **serializer.validated_data)
        return board_response(lunch.id, status.HTTP_201_CREATED)


class LunchView(CompanyAPIView):
    """GET /api/lunches/<id>: the full board; this is what a shared lunch link opens."""

    required_permissions = {"GET": Permission.SESSION_READ}

    def get(self, request, lunch_id):
        lunch = get_object_or_404(
            with_board(OrderSession.objects.all()),
            id=lunch_id,
            org_id=request.user.org_id,
        )
        return Response(LunchDetailSerializer(lunch).data)


class ItemsView(CompanyAPIView):
    """POST /api/lunches/<id>/items: pick a dish."""

    required_permissions = {"POST": Permission.PICK_CREATE}

    def post(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        serializer = ItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = services.add_item(lunch, request.user, serializer.validated_data)
        return Response(ItemSerializer(item).data, status=status.HTTP_201_CREATED)


class ItemView(CompanyAPIView):
    """PATCH or DELETE /api/lunches/<id>/items/<item_id>: change or drop your pick.

    Only your own picks: nobody can change someone else's.
    """

    required_permissions = {
        "PATCH": Permission.PICK_UPDATE,
        "DELETE": Permission.PICK_DELETE,
    }

    def patch(self, request, lunch_id, item_id):
        lunch = get_lunch_for_update(request, lunch_id)
        item = get_object_or_404(
            lunch.items.select_related("user"), id=item_id, user=request.user
        )
        serializer = ItemSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        item = services.update_item(lunch, item, serializer.validated_data)
        return Response(ItemSerializer(item).data)

    def delete(self, request, lunch_id, item_id):
        lunch = get_lunch_for_update(request, lunch_id)
        item = get_object_or_404(lunch.items, id=item_id, user=request.user)
        services.remove_item(lunch, item)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PicksView(CompanyAPIView):
    """PUT /api/lunches/<id>/picks: replace your picks, and complete them, at once."""

    required_permissions = {"PUT": Permission.PICK_CREATE}

    def put(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        serializer = PicksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.replace_picks(
            lunch,
            request.user,
            serializer.validated_data["items"],
            complete=serializer.validated_data["complete"],
        )
        return board_response(lunch.id)


class SkipView(CompanyAPIView):
    """PUT /api/lunches/<id>/skip: sit this one out. DELETE: join back in.

    Both answer with the board, so the app doesn't have to fetch it again.
    """

    required_permissions = {
        "PUT": Permission.PICK_UPDATE,
        "DELETE": Permission.PICK_UPDATE,
    }

    def put(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        services.skip(lunch, request.user)
        return board_response(lunch.id)

    def delete(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        services.unskip(lunch, request.user)
        return board_response(lunch.id)


class CompleteView(CompanyAPIView):
    """PUT /api/lunches/<id>/complete: your picks are final until the host reopens."""

    required_permissions = {"PUT": Permission.PICK_UPDATE}

    def put(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        services.complete(lunch, request.user)
        return board_response(lunch.id)


class ReopenView(CompanyAPIView):
    """DELETE /api/lunches/<id>/complete/<user_id>: let someone pick again."""

    required_permissions = {"DELETE": Permission.SESSION_UPDATE}

    def delete(self, request, lunch_id, user_id):
        lunch = get_lunch_for_update(request, lunch_id)
        person = get_object_or_404(User, id=user_id, org_id=request.user.org_id)
        services.reopen(lunch, person)
        return board_response(lunch.id)


class SkipPersonView(CompanyAPIView):
    """PUT /api/lunches/<id>/skip/<user_id>: the host marks someone as skipping."""

    required_permissions = {"PUT": Permission.SESSION_UPDATE}

    def put(self, request, lunch_id, user_id):
        lunch = get_lunch_for_update(request, lunch_id)
        person = get_object_or_404(User, id=user_id, org_id=request.user.org_id)
        services.skip(lunch, person)
        return board_response(lunch.id)


class LockView(CompanyAPIView):
    """POST /api/lunches/<id>/lock: close picks before the cutoff."""

    required_permissions = {"POST": Permission.SESSION_UPDATE}

    def post(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        services.lock(lunch)
        return board_response(lunch.id)


class UnlockView(CompanyAPIView):
    """POST /api/lunches/<id>/unlock: reopen picks after locking, before ordering."""

    required_permissions = {"POST": Permission.SESSION_UPDATE}

    def post(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        serializer = UnlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.unlock(lunch, serializer.validated_data.get("cutoff_at"))
        return board_response(lunch.id)


class CancelView(CompanyAPIView):
    """POST /api/lunches/<id>/cancel: call off a lunch not yet ordered."""

    required_permissions = {"POST": Permission.SESSION_DELETE}

    def post(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        services.cancel(lunch)
        return board_response(lunch.id)
