import hashlib
import secrets
from collections.abc import Iterable
from datetime import timedelta
from functools import partial
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, ValidationError

from apps.accounts import rbac
from apps.accounts.models import (
    HOST_ROLE,
    MEMBER_ROLE,
    Address,
    ApiToken,
    DeviceToken,
    Invite,
    Organization,
    OrgKind,
    User,
)
from apps.core import notify
from apps.core.errors import Conflict


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def is_public_email(email: str) -> bool:
    """Gmail and the like: anyone can have one, so they never make a company."""
    return email_domain(email) in settings.PUBLIC_EMAIL_DOMAINS


def user_for_token(claims: dict) -> User:
    """The user for verified Firebase token claims, created on first sign-in."""
    email = claims.get("email")
    if not email or not claims.get("email_verified"):
        raise AuthenticationFailed("Sign in with a verified Google account.")

    user, _ = User.objects.select_related("org").get_or_create(
        firebase_uid=claims["uid"],
        defaults={"email": email.lower(), "name": claims.get("name", "")},
    )
    if user.org_id is None and not is_public_email(user.email):
        org = Organization.objects.filter(
            kind=OrgKind.COMPANY, email_domain=email_domain(user.email)
        ).first()
        if org and _may_join(org, user.email):
            _set_company(user, org, MEMBER_ROLE)
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
    if org.kind == OrgKind.PERSONAL:
        raise Conflict("A personal party has no company to invite people to.")
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
    """Set up the signed-in person's company; they get the Host role (everything)."""
    if user.org_id:
        raise Conflict("You already belong to a company.")
    domain = email_domain(user.email)
    if is_public_email(user.email):
        raise Conflict(
            f"@{domain} is a public email address, so it can't make a company. "
            "Start a lunch and share its link instead."
        )
    if Organization.objects.filter(kind=OrgKind.COMPANY, email_domain=domain).exists():
        raise Conflict("A company for your email domain already exists.")
    org = Organization.objects.create(name=name, email_domain=domain, created_by=user)
    _set_company(user, org, HOST_ROLE)
    return org


def create_personal_org(user: User) -> Organization:
    """The signed-in person's own party, made when they first host. Safe to repeat.

    Only people with a public email get one: everyone else is in, or sets up, their
    company. They get the Host role in it.
    """
    User.objects.select_for_update().get(id=user.id)  # two starts at once wait here
    user.refresh_from_db(fields=["org"])
    if user.org is not None:
        return user.org
    if not is_public_email(user.email):
        raise Conflict("Set up or join your company first.")
    first = (user.name or user.email.split("@")[0]).split()[0].title()
    org = Organization.objects.create(
        name=f"{first}'s party",
        kind=OrgKind.PERSONAL,
        email_domain="",
        created_by=user,
    )
    _set_company(user, org, HOST_ROLE)
    return org


def _set_company(user: User, org: Organization, role_name: str) -> None:
    """Put a person in a company with one of its system roles (Host or Member)."""
    user.org = org
    user.save(update_fields=["org", "updated_at"])
    rbac.give_role(user, rbac.ensure_system_roles(org)[role_name])


def find_address(org_id: UUID, text: str) -> Address | None:
    return Address.objects.filter(org_id=org_id, address__iexact=text).first()


def save_address(
    org_id: UUID,
    text: str,
    lat: float | None = None,
    lng: float | None = None,
    *,
    saved: bool = True,
) -> tuple[Address, bool]:
    """The company's address with this text, made if it isn't there yet, and whether it
    is newly on the host's list. Coordinates fill in what an address lacks, and a place
    picked from the suggestions replaces what was typed. Safe to repeat."""
    for _ in range(2):  # a second try when someone made the same address a moment ago
        address = find_address(org_id, text)
        try:
            with transaction.atomic():
                if address is None:
                    address = Address.objects.create(
                        org_id=org_id, address=text, lat=lat, lng=lng, saved=saved
                    )
                    return address, saved
                return address, _update_address(address, lat, lng, saved=saved)
        except IntegrityError:
            continue
    raise Conflict("This address is being added right now. Try again.")


def _update_address(
    address: Address, lat: float | None, lng: float | None, *, saved: bool
) -> bool:
    fields = []
    if lat is not None and lng is not None and (address.lat, address.lng) != (lat, lng):
        address.lat, address.lng = lat, lng
        fields += ["lat", "lng"]
    newly_listed = saved and not address.saved
    if newly_listed:
        address.saved = True
        fields.append("saved")
    if fields:
        address.save(update_fields=[*fields, "updated_at"])
    return newly_listed


def unsave_address(address: Address) -> None:
    """Take an address off the host's list; delete it if no lunch went there."""
    if address.lunches.exists():
        address.saved = False
        address.save(update_fields=["saved", "updated_at"])
    else:
        address.delete()


def _personal_party_id(user: User) -> UUID | None:
    """The personal-party lunch this person is in, if any (whose topic they hear)."""
    from apps.lunches import services as lunches  # lunches builds on accounts

    party = lunches.running_party(user)
    if party is not None and party.org.kind == OrgKind.PERSONAL:
        return party.id
    return None


# Browsers a person can have notifications on at once; the least recently seen go first.
MAX_DEVICES = 10


def subscribe_device(user: User, token: str) -> None:
    """Remember a device and subscribe it to what its person should hear."""
    DeviceToken.objects.update_or_create(token=token, defaults={"user": user})
    newest = DeviceToken.objects.filter(user=user).order_by("-updated_at")
    keep = list(newest.values_list("id", flat=True)[:MAX_DEVICES])
    DeviceToken.objects.filter(user=user).exclude(id__in=keep).delete()
    if user.org_id and user.org and user.org.kind == OrgKind.COMPANY:
        notify.subscribe_to_company(token, user.org_id)
    if party_id := _personal_party_id(user):
        notify.subscribe_to_lunch([token], party_id)


def unsubscribe_device(user: User, token: str) -> None:
    """Stop notifications on a device: forget it and leave the topics it joined."""
    DeviceToken.objects.filter(user=user, token=token).delete()
    if user.org_id and user.org and user.org.kind == OrgKind.COMPANY:
        notify.unsubscribe_from_company([token], user.org_id)
    if party_id := _personal_party_id(user):
        notify.unsubscribe_from_lunch([token], party_id)


TOKEN_PREFIX = "lc_"  # noqa: S105  # nosec B105 (what tokens start with)
# last_used_at is refreshed at most this often, so a busy assistant isn't a write per
# call.
TOUCH_EVERY = timedelta(minutes=1)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_api_token(user: User, name: str, lifetime: timedelta) -> tuple[ApiToken, str]:
    """Make a token that lasts `lifetime`: the short-lived token an assistant gets by
    signing in (apps/oauth); each one replaces the one before it."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken.objects.create(
        user=user,
        name=name.strip()[:60],
        prefix=token[:11],
        token_hash=_hash(token),
        expires_at=timezone.now() + lifetime,
    )
    return row, token


def user_for_api_token(token: str) -> User:
    """The person an assistant's token belongs to, or AuthenticationFailed."""
    row = (
        ApiToken.objects.select_related("user", "user__org")
        .filter(token_hash=_hash(token))
        .first()
    )
    if row is None or row.revoked_at is not None:
        raise AuthenticationFailed(
            "That access token isn't valid. Connect the assistant again."
        )
    now = timezone.now()
    if row.expires_at is not None and row.expires_at <= now:
        raise AuthenticationFailed(
            "That access token has expired. Connect the assistant again."
        )
    if row.last_used_at is None or now - row.last_used_at > TOUCH_EVERY:
        ApiToken.objects.filter(id=row.id).update(last_used_at=now)
    return row.user
