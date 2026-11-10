from collections.abc import Iterable
from functools import partial
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import (
    AuthenticationFailed,
    PermissionDenied,
    ValidationError,
)

from apps.accounts.models import (
    ALL_PERMISSIONS,
    MEMBER_PERMISSIONS,
    Invite,
    Organization,
    Permission,
    User,
)
from apps.core import notify
from apps.core.errors import Conflict


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def user_for_token(claims: dict) -> User:
    """The user for verified Firebase token claims, created on first sign-in."""
    email = claims.get("email")
    if not email or not claims.get("email_verified"):
        raise AuthenticationFailed("Sign in with a verified Google account.")

    user, _ = User.objects.select_related("org").get_or_create(
        firebase_uid=claims["uid"],
        defaults={"email": email.lower(), "name": claims.get("name", "")},
    )
    if user.org_id is None:
        org = Organization.objects.filter(email_domain=email_domain(user.email)).first()
        if org and _may_join(org, user.email):
            _set_company(user, org, MEMBER_PERMISSIONS)
    return user


def _may_join(org: Organization, email: str) -> bool:
    """Anyone from the company's domain may join, or only those invited when
    auto_join is off. An invite counts as accepted either way."""
    invited = Invite.objects.filter(
        org=org, email=email, accepted_at__isnull=True
    ).update(accepted_at=timezone.now())
    return org.auto_join or invited > 0


def invite(inviter: User, emails: Iterable[str]) -> list[Invite]:
    """Invite people from the company's email domain and email each a sign-in link.

    People already in the company are left out; inviting someone twice is harmless.
    """
    org = inviter.org
    if org is None:  # views only let people with a company in
        raise Conflict("Set up or join your company first.")
    wanted = sorted({email.lower() for email in emails})
    outside = [e for e in wanted if email_domain(e) != org.email_domain]
    if outside:
        raise ValidationError(
            {
                "emails": [
                    f"Only @{org.email_domain} addresses can join {org.name}: "
                    f"{', '.join(outside)}."
                ]
            }
        )
    members = set(
        User.objects.filter(org=org, email__in=wanted).values_list("email", flat=True)
    )
    invites, new = [], []
    for email in wanted:
        if email in members:
            continue
        item, created = Invite.objects.get_or_create(
            org=org, email=email, defaults={"invited_by": inviter}
        )
        invites.append(item)
        if created:
            new.append(email)
    _email_invites(inviter, org, new)
    return invites


def _email_invites(inviter: User, org: Organization, emails: list[str]) -> None:
    who = inviter.name or inviter.email
    subject = f"{who} invited you to lunch with {org.name}"
    body = (
        f"{who} uses Lunch Board for group lunch. Sign in with your "
        f"@{org.email_domain} Google account to pick your food: {settings.APP_URL}"
    )
    for email in emails:  # one each, so nobody sees the others' addresses
        transaction.on_commit(partial(notify.email, [email], subject, body))


def dev_user_claims(email: str) -> dict:
    """Stand-in token claims for DEV_AUTH_EMAIL (local development only)."""
    return {
        "uid": f"dev-{email}",
        "email": email,
        "email_verified": True,
        "name": email.split("@")[0].replace(".", " ").title(),
    }


def create_company(user: User, name: str) -> Organization:
    """Set up the signed-in person's company; they get every permission in it."""
    if user.org_id:
        raise Conflict("You already belong to a company.")
    domain = email_domain(user.email)
    if Organization.objects.filter(email_domain=domain).exists():
        raise Conflict("A company for your email domain already exists.")
    org = Organization.objects.create(name=name, email_domain=domain)
    _set_company(user, org, ALL_PERMISSIONS)
    return org


def set_permissions(granter: User, member: User, permissions: Iterable[str]) -> User:
    """Replace a teammate's permissions.

    People can only grant or remove permissions they have themselves, and someone in
    the company must keep member_update so that permissions can still be changed.
    """
    wanted = set(permissions)
    people = _lock_permission_holders(granter, member)
    granter, member = people[granter.id], people[member.id]
    if not granter.has_perm(Permission.MEMBER_UPDATE):  # lost it a moment ago
        raise PermissionDenied(
            f"You need the {Permission.MEMBER_UPDATE} permission to do this."
        )
    beyond_granter = (wanted ^ set(member.permissions)) - set(granter.permissions)
    if beyond_granter:
        raise PermissionDenied(
            "You can only grant or remove permissions you have yourself: "
            f"{', '.join(sorted(beyond_granter))}."
        )
    others_manage = any(
        person.id != member.id and person.has_perm(Permission.MEMBER_UPDATE)
        for person in people.values()
    )
    if Permission.MEMBER_UPDATE not in wanted and not others_manage:
        raise Conflict(
            f"Someone in the company must keep {Permission.MEMBER_UPDATE}, so that "
            "permissions can still be changed."
        )
    member.permissions = sorted(wanted)
    member.save(update_fields=["permissions", "updated_at"])
    _sync_claims(member)
    return member


def _lock_permission_holders(granter: User, member: User) -> dict[UUID, User]:
    """Lock both people and everyone who can change permissions, always in id order.

    Two people taking member_update from each other at the same moment then run one
    after the other, and the second sees that nobody else would be left with it.
    """
    holders = (
        User.objects.select_for_update()
        .filter(
            Q(id__in=[granter.id, member.id])
            | Q(org_id=member.org_id, permissions__contains=[Permission.MEMBER_UPDATE])
        )
        .order_by("id")
    )
    return {person.id: person for person in holders}


def _set_company(user: User, org: Organization, permissions: Iterable[str]) -> None:
    user.org = org
    user.permissions = sorted(permissions)
    user.save(update_fields=["org", "permissions", "updated_at"])
    _sync_claims(user)


def _sync_claims(user: User) -> None:
    """Copy the company and permissions onto the Firebase account, for Firestore."""
    if not settings.FIRESTORE_ENABLED:
        return
    uid = user.firebase_uid
    claims = {"org_id": str(user.org_id), "permissions": sorted(user.permissions)}
    transaction.on_commit(lambda: _set_claims(uid, claims))


def _set_claims(firebase_uid: str, claims: dict) -> None:
    """Firestore rules read these to show boards only to people allowed to see them."""
    from firebase_admin import auth

    from apps.core import firebase

    auth.set_custom_user_claims(firebase_uid, claims, app=firebase.app())
