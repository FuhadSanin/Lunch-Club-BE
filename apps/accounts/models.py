from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class RestaurantMode(models.TextChoices):
    NEARBY = "nearby"  # every restaurant within radius_km of the office
    MANUAL = "manual"  # the host picks them


class Organization(BaseModel):
    """A company. People who sign in with an @email_domain address join it: anyone, or
    with `auto_join` off only those invited."""

    name = models.CharField(max_length=120)
    team_name = models.CharField(max_length=120, blank=True)
    email_domain = models.CharField(max_length=253, unique=True)
    auto_join = models.BooleanField(default=True)
    # How a new lunch chooses its restaurants, and what "nearby" means.
    restaurant_mode = models.CharField(
        max_length=10, choices=RestaurantMode.choices, default=RestaurantMode.NEARBY
    )
    radius_km = models.FloatField(
        default=3.0, validators=[MinValueValidator(0.5), MaxValueValidator(8.0)]
    )
    # Where restaurants and menus are looked up from; empty until the host sets it.
    office_lat = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    office_lng = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )

    class Meta:
        db_table = "organizations"
        constraints = [
            models.CheckConstraint(
                condition=Q(radius_km__gte=0.5, radius_km__lte=8.0),
                name="organizations_radius_range",
            ),
            models.CheckConstraint(
                condition=Q(office_lat__isnull=True, office_lng__isnull=True)
                | Q(office_lat__isnull=False, office_lng__isnull=False),
                name="organizations_office_both_or_neither",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Permission(models.TextChoices):
    """Everything a person can be allowed to do, named `<resource>_<action>`.

    Each API endpoint names the permission it needs (see `required_permissions` on the
    views); the label says what it allows, for the screen where people get them.
    """

    SESSION_CREATE = "session_create", "Start a lunch"
    SESSION_READ = "session_read", "See lunches and their live boards"
    SESSION_UPDATE = "session_update", "Lock a lunch's picks before the cutoff"
    SESSION_DELETE = "session_delete", "Cancel a lunch that isn't ordered yet"
    PICK_CREATE = "pick_create", "Pick dishes for a lunch"
    PICK_UPDATE = "pick_update", "Change their picks, or skip a lunch"
    PICK_DELETE = "pick_delete", "Remove their picks"
    REVIEW_CREATE = (
        "review_create",
        "Confirm a locked lunch's cart, so its orders go out",
    )
    ORDER_UPDATE = (
        "order_update",
        "Enter Swiggy order numbers and mark orders delivered",
    )
    RESTAURANT_READ = "restaurant_read", "Browse Swiggy restaurants, menus and dishes"
    MEMBER_CREATE = "member_create", "Invite people to the company"
    MEMBER_READ = (
        "member_read",
        "See everyone in the company, their permissions and invites",
    )
    MEMBER_UPDATE = "member_update", "Grant and remove people's permissions"
    MEMBER_DELETE = "member_delete", "Withdraw invites"
    COMPANY_UPDATE = (
        "company_update",
        "Change the company's settings and office addresses",
    )


# What people get when they join a company. Whoever sets the company up gets everything.
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
    # What this person may do in their company; empty until they have one.
    permissions = ArrayField(
        models.CharField(max_length=40, choices=Permission.choices),
        default=list,
        blank=True,
    )

    # Read by Django REST framework's permission checks.
    is_authenticated = True

    class Meta:
        db_table = "users"
        constraints = [
            models.CheckConstraint(
                condition=Q(permissions__contained_by=Permission.values),
                name="users_permissions_known",
            )
        ]

    def __str__(self) -> str:
        return self.email

    def has_perm(self, permission: str) -> bool:
        return permission in self.permissions


class OfficeAddress(BaseModel):
    """Where lunch can be delivered; the host picks one when starting a lunch."""

    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="addresses"
    )
    address = models.CharField(max_length=300)

    class Meta:
        db_table = "office_addresses"
        constraints = [
            models.UniqueConstraint(
                fields=["org", "address"], name="office_addresses_unique"
            )
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
