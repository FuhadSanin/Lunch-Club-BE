from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts import services as accounts
from apps.accounts.models import Permission, User
from apps.accounts.permissions import (
    CompanyAPIView,
    MayStartOwnLunch,
    ParticipantAPIView,
)
from apps.core import tenancy
from apps.lunches import services
from apps.lunches.models import OrderSession, SessionMember
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


def participant_lunches(user, lunches=None):
    """The lunches a person takes part in: their org's, and the ones they joined.

    The database already shows them nothing else (row-level security); this says it in
    the query too, so a mistake in one place can't open another company's lunch.
    """
    joined = SessionMember.objects.filter(user=user).values("session_id")
    lunches = OrderSession.objects.all() if lunches is None else lunches
    return lunches.filter(Q(org_id=user.org_id) | Q(id__in=joined))


def get_participant_lunch(view, request, lunch_id) -> OrderSession:
    """Fetch a lunch the caller takes part in and lock its row until the request
    finishes."""
    lunch = get_object_or_404(
        participant_lunches(
            request.user,
            OrderSession.objects.select_for_update(of=("self",)).select_related(
                "host", "org"
            ),
        ),
        id=lunch_id,
    )
    view.check_on_lunch(request, lunch)
    return lunch


def board_response(lunch_id, status_code=status.HTTP_200_OK, user=None) -> Response:
    lunch = with_board(OrderSession.objects.filter(id=lunch_id)).get()
    data = LunchDetailSerializer(lunch, context={"user": user}).data
    return Response(data, status=status_code)


class LunchesView(ParticipantAPIView):
    """GET /api/lunches: recent lunches, yours and the ones you joined. POST: start one.

    A person with a public email who has no org yet gets their personal party when they
    start their first lunch.
    """

    permission_classes = [IsAuthenticated, MayStartOwnLunch]
    required_permissions = {
        "GET": Permission.SESSION_READ,
        "POST": Permission.SESSION_CREATE,
    }

    def get(self, request):
        user = request.user
        if user.org_id is not None and not user.has_perm(Permission.SESSION_READ):
            self.permission_denied(
                request, message="You need the session_read permission to do this."
            )
        lunches = participant_lunches(user).select_related("host", "org")
        lunches = lunches.order_by("-created_at")[:RECENT_LUNCHES]
        return Response(
            LunchSerializer(lunches, many=True, context={"user": user}).data
        )

    def post(self, request):
        user = request.user
        serializer = StartLunchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if user.org_id is None:
            services.ensure_free(user)  # before making their party, not after
            accounts.create_personal_org(user)
            tenancy.activate(user.org_id, user.id)  # the party is new
        lunch = services.start(user, **serializer.validated_data)
        return board_response(lunch.id, status.HTTP_201_CREATED, user)


class LunchView(ParticipantAPIView):
    """GET /api/lunches/<id>: the full board; this is what a shared lunch link opens."""

    required_permissions = {"GET": Permission.SESSION_READ}

    def get(self, request, lunch_id):
        lunch = get_object_or_404(
            participant_lunches(request.user, with_board(OrderSession.objects.all())),
            id=lunch_id,
        )
        self.check_on_lunch(request, lunch)
        data = LunchDetailSerializer(lunch, context={"user": request.user}).data
        return Response(data)


class JoinView(ParticipantAPIView):
    """POST /api/lunches/<id>/join: take part in a lunch from its shared link.
    DELETE: leave a lunch joined that way (its picks are dropped).

    Anyone signed in can join a personal party's lunch; a company's lunch only its own
    people. POST answers with the board; DELETE with nothing.
    """

    required_permissions = {"POST": None, "DELETE": None}  # depends on the lunch

    def post(self, request, lunch_id):
        lunch = services.join(request.user, lunch_id)
        return board_response(lunch.id, user=request.user)

    def delete(self, request, lunch_id):
        lunch = get_participant_lunch(self, request, lunch_id)
        services.leave(lunch, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ItemsView(ParticipantAPIView):
    """POST /api/lunches/<id>/items: pick a dish."""

    required_permissions = {"POST": Permission.PICK_CREATE}

    def post(self, request, lunch_id):
        lunch = get_participant_lunch(self, request, lunch_id)
        serializer = ItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = services.add_item(lunch, request.user, serializer.validated_data)
        return Response(ItemSerializer(item).data, status=status.HTTP_201_CREATED)


class ItemView(ParticipantAPIView):
    """PATCH or DELETE /api/lunches/<id>/items/<item_id>: change or drop your pick.

    Only your own picks: nobody can change someone else's.
    """

    required_permissions = {
        "PATCH": Permission.PICK_UPDATE,
        "DELETE": Permission.PICK_DELETE,
    }

    def patch(self, request, lunch_id, item_id):
        lunch = get_participant_lunch(self, request, lunch_id)
        item = get_object_or_404(
            lunch.items.select_related("user"), id=item_id, user=request.user
        )
        serializer = ItemSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        item = services.update_item(lunch, item, serializer.validated_data)
        return Response(ItemSerializer(item).data)

    def delete(self, request, lunch_id, item_id):
        lunch = get_participant_lunch(self, request, lunch_id)
        item = get_object_or_404(lunch.items, id=item_id, user=request.user)
        services.remove_item(lunch, item)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PicksView(ParticipantAPIView):
    """PUT /api/lunches/<id>/picks: replace your picks, and complete them, at once."""

    required_permissions = {"PUT": Permission.PICK_CREATE}

    def put(self, request, lunch_id):
        lunch = get_participant_lunch(self, request, lunch_id)
        serializer = PicksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.replace_picks(
            lunch,
            request.user,
            serializer.validated_data["items"],
            complete=serializer.validated_data["complete"],
        )
        return board_response(lunch.id, user=request.user)


class SkipView(ParticipantAPIView):
    """PUT /api/lunches/<id>/skip: sit this one out. DELETE: join back in.

    Both answer with the board, so the app doesn't have to fetch it again.
    """

    required_permissions = {
        "PUT": Permission.PICK_UPDATE,
        "DELETE": Permission.PICK_UPDATE,
    }

    def put(self, request, lunch_id):
        lunch = get_participant_lunch(self, request, lunch_id)
        services.skip(lunch, request.user)
        return board_response(lunch.id, user=request.user)

    def delete(self, request, lunch_id):
        lunch = get_participant_lunch(self, request, lunch_id)
        services.unskip(lunch, request.user)
        return board_response(lunch.id, user=request.user)


class CompleteView(ParticipantAPIView):
    """PUT /api/lunches/<id>/complete: your picks are final until the host reopens."""

    required_permissions = {"PUT": Permission.PICK_UPDATE}

    def put(self, request, lunch_id):
        lunch = get_participant_lunch(self, request, lunch_id)
        services.complete(lunch, request.user)
        return board_response(lunch.id, user=request.user)


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


class CloseView(CompanyAPIView):
    """POST /api/lunches/<id>/close: end a lunch whose food has arrived."""

    required_permissions = {"POST": Permission.SESSION_UPDATE}

    def post(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        services.close(lunch)
        return board_response(lunch.id)


class CancelView(CompanyAPIView):
    """POST /api/lunches/<id>/cancel: call off a lunch not yet ordered."""

    required_permissions = {"POST": Permission.SESSION_DELETE}

    def post(self, request, lunch_id):
        lunch = get_lunch_for_update(request, lunch_id)
        services.cancel(lunch)
        return board_response(lunch.id)
