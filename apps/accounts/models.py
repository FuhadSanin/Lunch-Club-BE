from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.core.models import BaseModel


class OrgKind(models.TextChoices):
    COMPANY = "company"  # people sign in with the company's email domain
    PERSONAL = "personal"  # one person's party: they host, others join by link


class Organization(BaseModel):
    """A company. People who sign in with an @email_domain address join it: anyone, or
    with `auto_join` off only those invited.

    A personal org belongs to one person with a public email (Gmail and the like); it
    has no email domain, and people reach its lunches by joining them, not by address.
    """

    name = models.CharField(max_length=120)
    team_name = models.CharField(max_length=120, blank=True)
    kind = models.CharField(
        max_length=10, choices=OrgKind.choices, default=OrgKind.COMPANY
    )
    # Unique among companies only; a personal org leaves it empty.
    email_domain = models.CharField(max_length=253, blank=True)
    auto_join = models.BooleanField(default=True)
    # Who set the company up (or whose party it is). Empty if that person is gone.
    created_by = models.ForeignKey(
        "User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "organizations"
        constraints = [
            models.UniqueConstraint(
                fields=["email_domain"],
                condition=Q(kind="company"),
                name="organizations_company_domain_unique",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Permission(models.TextChoices):
    """Everything a person can be allowed to do, named `<resource>_<action>`.

    People get permissions through roles (`Role`). Each API endpoint names the
    permission it needs (see `required_permissions` on the views); the label says what
    it allows, for the screen where roles are made.
    """

    SESSION_CREATE = "session_create", "Start a party"
    SESSION_READ = "session_read", "See parties and their live boards"
    SESSION_UPDATE = "session_update", "Lock a party's picks before the cutoff"
    SESSION_DELETE = "session_delete", "Cancel a party that isn't ordered yet"
    PICK_CREATE = "pick_create", "Pick dishes for a party"
    PICK_UPDATE = "pick_update", "Change their picks, or skip a party"
    PICK_DELETE = "pick_delete", "Remove their picks"
    REVIEW_CREATE = (
        "review_create",
        "Confirm a locked party's cart, so its orders go out",
    )
    ORDER_UPDATE = (
        "order_update",
        "Enter Swiggy order numbers and mark orders delivered",
    )
    RESTAURANT_READ = "restaurant_read", "Browse Swiggy restaurants, menus and dishes"
    MEMBER_CREATE = "member_create", "Invite people to the company"
    MEMBER_READ = (
        "member_read",
        "See everyone in the company, their roles and invites",
    )
    MEMBER_UPDATE = "member_update", "Make roles and give them to people"
    MEMBER_DELETE = "member_delete", "Withdraw invites"
    COMPANY_UPDATE = (
        "company_update",
        "Change the company's settings and addresses",
    )


# What the Member role grants. Whoever sets a company up gets the Host role, which
# grants everything.
MEMBER_PERMISSIONS = frozenset(
    {
        Permission.SESSION_READ,
        Permission.PICK_CREATE,
        Permission.PICK_UPDATE,
        Permission.PICK_DELETE,
        Permission.RESTAURANT_READ,
        Permission.MEMBER_READ,
    }
)
ALL_PERMISSIONS = frozenset(Permission)

HOST_ROLE = "Host"
MEMBER_ROLE = "Member"


class Role(BaseModel):
    """A named set of permissions inside one company, like "Auditor".

    Every company has the two system roles, Host and Member, which can't be changed.
    People with `member_update` can make more, and give people roles: what a person may
    do is the union of the permissions of their roles (`RolePermission`, `UserRole`).
    """

    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="roles"
    )
    name = models.CharField(max_length=60)
    # Host and Member: made with the company, never renamed, edited or deleted.
    is_system = models.BooleanField(default=False)

    class Meta:
        db_table = "roles"
        constraints = [
            models.UniqueConstraint(Lower("name"), "org", name="roles_org_name_unique")
        ]

    def __str__(self) -> str:
        return self.name


class RolePermission(BaseModel):
    """One permission a role grants."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="grants")
    permission = models.CharField(max_length=40, choices=Permission.choices)

    class Meta:
        db_table = "role_permissions"
        constraints = [
            models.UniqueConstraint(
                fields=["role", "permission"], name="role_permissions_unique"
            ),
            models.CheckConstraint(
                condition=Q(permission__in=Permission.values),
                name="role_permissions_known",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.role_id} {self.permission}"


class User(BaseModel):
    # Empty until the person's company is set up.
    org = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="users",
    )
    firebase_uid = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(unique=True)
    # What this person is in their company; they get no permissions without one. The
    # roles are all of the person's own company (the services keep it so).
    roles = models.ManyToManyField(Role, through="UserRole", related_name="users")

    # Read by Django REST framework's permission checks.
    is_authenticated = True

    class Meta:
        db_table = "users"

    def __str__(self) -> str:
        return self.email

    @property
    def permissions(self) -> list[str]:
        """Everything this person may do: what their roles grant, together.

        Read once per loaded person (or from `prefetch_related("roles__grants")`), and
        again after `refresh_from_db`.
        """
        cached = self.__dict__.get("_permissions")
        if cached is None:
            if "roles" in getattr(self, "_prefetched_objects_cache", {}):
                granted = {
                    grant.permission
                    for role in self.roles.all()
                    for grant in role.grants.all()
                }
            else:  # one query, however many roles
                granted = set(
                    RolePermission.objects.filter(
                        role__user_roles__user_id=self.id
                    ).values_list("permission", flat=True)
                )
            cached = self.__dict__["_permissions"] = sorted(granted)
        return cached

    def has_perm(self, permission: str) -> bool:
        return permission in self.permissions

    def refresh_from_db(self, *args, **kwargs) -> None:
        self.__dict__.pop("_permissions", None)
        super().refresh_from_db(*args, **kwargs)


class UserRole(BaseModel):
    """A person has a role."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="user_roles")

    class Meta:
        db_table = "user_roles"
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="user_roles_unique")
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.role_id}"


class Address(BaseModel):
    """A place lunch is delivered to: the one table for every address.

    A lunch points at its address, so where it was delivered is kept even after the
    host takes the address off their list (`saved`). Coordinates are there when the
    address was picked from the place suggestions; restaurants are looked up near them.
    """

    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="addresses"
    )
    address = models.CharField(max_length=300)
    lat = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    lng = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    # On the host's list of addresses to choose from. A lunch's address stays when it
    # isn't (and one no lunch used is deleted instead of unsaved).
    saved = models.BooleanField(default=True)

    class Meta:
        db_table = "addresses"
        verbose_name_plural = "addresses"
        constraints = [
            models.UniqueConstraint(
                Lower("address"), "org", name="addresses_org_address_unique"
            ),
            models.CheckConstraint(
                condition=Q(lat__isnull=True, lng__isnull=True)
                | Q(lat__isnull=False, lng__isnull=False),
                name="addresses_point_both_or_neither",
            ),
        ]

    def __str__(self) -> str:
        return self.address


class Invite(BaseModel):
    """Someone asked to join. With `auto_join` off, only invited people can."""

    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invites"
    )
    email = models.EmailField()
    invited_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "invites"
        constraints = [
            models.UniqueConstraint(fields=["org", "email"], name="invites_unique")
        ]

    def __str__(self) -> str:
        return self.email


class DeviceToken(BaseModel):
    """A browser that turned on notifications (its Firebase Cloud Messaging token).

    Kept so a device can be subscribed to a lunch's notifications when its person
    joins that lunch, not only when they first turn notifications on.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    token = models.CharField(max_length=4096, unique=True)

    class Meta:
        db_table = "device_tokens"

    def __str__(self) -> str:
        return f"{self.user_id} {self.token[:12]}"


class Notification(BaseModel):
    """Something a person was told about a lunch, kept so they can read it later.

    The bell in the web app lists these; the push (see apps.core.notify) is the same
    message arriving while the app is closed.
    """

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="notifications"
    )
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=400, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="notifications_recent")
        ]

    def __str__(self) -> str:
        return self.title


class ApiToken(BaseModel):
    """The access token an AI assistant got by signing in (apps/oauth): it lets it,
    through the MCP server, act as one person without a Google sign-in.

    Only a SHA-256 hash is stored; the token itself is handed over once, when it is
    made. The prefix (`lc_` and a few characters) recognises a token in the list.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=60)
    prefix = models.CharField(max_length=16)
    token_hash = models.CharField(max_length=64, unique=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "api_tokens"

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"
