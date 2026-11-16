"""Roles and permissions: who may do what inside a company.

A role is a named set of permissions (`Role`, `RolePermission`), and a person has roles
(`UserRole`); what they may do is everything their roles grant together. Every company
has two system roles, Host (everything) and Member (picking food), made with the
company. People with `member_update` make others, and give people roles.

The rules, kept here so every way in obeys them:

- Nobody can give or take away a permission they don't have themselves.
- Someone in the company always keeps `member_update`, so roles can still be changed.
- Host and Member can't be changed; a role that people have can't be deleted.
- A person only gets roles of their own company.

Every change takes a lock on the company first, so two people changing roles at the
same moment run one after the other and each sees what the other did.
"""

from collections.abc import Iterable
from uuid import UUID

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.messages import not_allowed
from apps.accounts.models import (
    ALL_PERMISSIONS,
    HOST_ROLE,
    MEMBER_PERMISSIONS,
    MEMBER_ROLE,
    Organization,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from apps.core.errors import Conflict

SYSTEM_ROLES = {HOST_ROLE: ALL_PERMISSIONS, MEMBER_ROLE: MEMBER_PERMISSIONS}


def ensure_system_roles(org: Organization) -> dict[str, Role]:
    """Host and Member for this company, made if they aren't there. Safe to repeat."""
    roles = {}
    for name, permissions in SYSTEM_ROLES.items():
        role, created = Role.objects.get_or_create(
            org=org, name=name, defaults={"is_system": True}
        )
        if created:
            _grant(role, permissions)
        roles[name] = role
    return roles


def _grant(role: Role, permissions: Iterable[str]) -> None:
    RolePermission.objects.bulk_create(
        RolePermission(role=role, permission=permission)
        for permission in sorted(set(permissions))
    )


def give_role(user: User, role: Role) -> None:
    """Give a person a role of their company, and tell Firebase. No rules checked:
    for the server's own use (a new company's first person, someone joining)."""
    UserRole.objects.get_or_create(user=user, role=role)
    user.refresh_from_db()
    _sync_claims(user)


def role_by_permissions(org: Organization, permissions: Iterable[str]) -> Role:
    """A role of this company that grants exactly these permissions, made if needed.

    Host and Member when they match; otherwise a role named after what it can do."""
    wanted = frozenset(permissions)
    roles = ensure_system_roles(org)
    for role in roles.values():
        if SYSTEM_ROLES[role.name] == wanted:
            return role
    for role in Role.objects.filter(org=org, is_system=False).prefetch_related(
        "grants"
    ):
        if {grant.permission for grant in role.grants.all()} == wanted:
            return role
    role = Role.objects.create(org=org, name=_free_name(org, "Custom"))
    _grant(role, wanted)
    return role


def _free_name(org: Organization, base: str) -> str:
    taken = {
        n.lower() for n in Role.objects.filter(org=org).values_list("name", flat=True)
    }
    number = 1
    while f"{base} {number}".lower() in taken:
        number += 1
    return f"{base} {number}"


def _lock_company(user: User) -> UUID:
    """Wait for anyone else changing this person's company's roles; its id."""
    if user.org_id is None:  # views only let people with a company in
        raise Conflict("Set up or join your company first.")
    Organization.objects.select_for_update().get(id=user.org_id)
    return user.org_id


def _held(granter: User, permissions: Iterable[str]) -> None:
    """People can only hand out what they have."""
    beyond = set(permissions) - set(granter.permissions)
    if beyond:
        raise PermissionDenied(
            "You can only grant or remove permissions you have yourself: "
            f"{', '.join(sorted(beyond))}."
        )


def _someone_manages(org_id: UUID) -> bool:
    return UserRole.objects.filter(
        user__org_id=org_id,
        role__grants__permission=Permission.MEMBER_UPDATE,
    ).exists()


def _keep_a_manager(org_id: UUID) -> None:
    if not _someone_manages(org_id):
        raise Conflict(
            f"Someone in the company must keep {Permission.MEMBER_UPDATE}, so that "
            "roles can still be changed."
        )


def _ensure_allowed(actor: User) -> None:
    """The actor may still change roles (they could have lost it a moment ago)."""
    actor.refresh_from_db()
    if not actor.has_perm(Permission.MEMBER_UPDATE):
        raise PermissionDenied(not_allowed(Permission.MEMBER_UPDATE))


def _granted(role: Role) -> set[str]:
    return set(role.grants.values_list("permission", flat=True))


def _name_taken(org_id: UUID, name: str, *, except_role: Role | None = None) -> bool:
    taken = Role.objects.filter(org_id=org_id, name__iexact=name)
    if except_role is not None:
        taken = taken.exclude(id=except_role.id)
    return taken.exists()


def create_role(actor: User, name: str, permissions: Iterable[str]) -> Role:
    """Make a role for the actor's company, with only permissions the actor has."""
    wanted = set(permissions)
    with transaction.atomic():
        org_id = _lock_company(actor)
        _ensure_allowed(actor)
        _held(actor, wanted)
        if _name_taken(org_id, name):
            raise Conflict(f"There is already a role called {name}.")
        role = Role.objects.create(org_id=org_id, name=name)
        _grant(role, wanted)
    return role


def update_role(
    actor: User,
    role: Role,
    name: str | None = None,
    permissions: Iterable[str] | None = None,
) -> Role:
    """Rename a role and/or change what it grants. Everyone who has it is affected."""
    with transaction.atomic():
        _lock_company(actor)
        _ensure_allowed(actor)
        if role.is_system:
            raise Conflict(f"{role.name} is built in and can't be changed.")
        if name is not None and name != role.name:
            if _name_taken(role.org_id, name, except_role=role):
                raise Conflict(f"There is already a role called {name}.")
            role.name = name
            role.save(update_fields=["name", "updated_at"])
        if permissions is not None:
            wanted, now = set(permissions), _granted(role)
            _held(actor, wanted ^ now)
            role.grants.exclude(permission__in=wanted).delete()
            _grant(role, wanted - now)
            _keep_a_manager(role.org_id)
            _sync_holders(role)
    return role


def delete_role(actor: User, role: Role) -> None:
    """Delete a role nobody has."""
    with transaction.atomic():
        _lock_company(actor)
        _ensure_allowed(actor)
        if role.is_system:
            raise Conflict(f"{role.name} is built in and can't be deleted.")
        holders = role.user_roles.count()
        if holders:
            raise Conflict(
                f"{holders} {'person has' if holders == 1 else 'people have'} this "
                "role. Give them another role first."
            )
        role.delete()


def set_roles(actor: User, member: User, role_ids: Iterable[UUID]) -> User:
    """Replace a person's roles with these (all of the company's own)."""
    wanted_ids = set(role_ids)
    with transaction.atomic():
        org_id = _lock_company(member)
        _ensure_allowed(actor)
        roles = list(
            Role.objects.filter(
                org_id=member.org_id, id__in=wanted_ids
            ).prefetch_related("grants")
        )
        if len(roles) != len(wanted_ids):
            raise ValidationError({"roles": ["Choose roles of your own company."]})
        member.refresh_from_db()
        before = set(member.permissions)
        UserRole.objects.filter(user=member).exclude(role__in=roles).delete()
        for role in roles:
            UserRole.objects.get_or_create(user=member, role=role)
        member.refresh_from_db()
        _held(actor, before ^ set(member.permissions))
        _keep_a_manager(org_id)
    _sync_claims(member)
    return member


def _sync_holders(role: Role) -> None:
    for person in role.users.all():
        person.refresh_from_db()
        _sync_claims(person)


def _sync_claims(user: User) -> None:
    """Copy the company and permissions onto the Firebase account, for Firestore."""
    if not settings.FIRESTORE_ENABLED:
        return
    uid = user.firebase_uid
    claims = {"org_id": str(user.org_id), "permissions": list(user.permissions)}
    transaction.on_commit(lambda: _set_claims(uid, claims))


def _set_claims(firebase_uid: str, claims: dict) -> None:
    """Firestore rules read these to show boards only to people allowed to see them."""
    from firebase_admin import auth

    from apps.core import firebase

    auth.set_custom_user_claims(firebase_uid, claims, app=firebase.app())
