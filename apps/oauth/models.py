from django.db import models

from apps.accounts.models import ApiToken, User
from apps.core.models import BaseModel


class OAuthClient(BaseModel):
    """An app that registered itself to ask people for access (Claude, Cursor, ...).

    Registration is open (RFC 7591), so a client's name is whatever it said: the consent
    screen shows where the person will be sent back to as well. Clients are public (they
    hold no secret); PKCE is what ties a code to the app that asked for it.
    """

    client_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    redirect_uris = models.JSONField(default=list)

    class Meta:
        db_table = "oauth_clients"

    def __str__(self) -> str:
        return self.name


class OAuthRequest(BaseModel):
    """One sign-in an app started: pending until the person decides, then it carries the
    one-time code the app trades for tokens. Codes are stored hashed and work once."""

    client = models.ForeignKey(
        OAuthClient, on_delete=models.CASCADE, related_name="requests"
    )
    redirect_uri = models.CharField(max_length=300)
    state = models.CharField(max_length=512, blank=True)
    code_challenge = models.CharField(max_length=128)
    scope = models.CharField(max_length=50, default="lunch")
    resource = models.CharField(max_length=300, blank=True)
    expires_at = models.DateTimeField()  # for the person to decide
    user = models.ForeignKey(
        User, null=True, on_delete=models.CASCADE, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    denied_at = models.DateTimeField(null=True, blank=True)
    code_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    code_expires_at = models.DateTimeField(null=True, blank=True)
    code_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "oauth_requests"


class OAuthRefreshToken(BaseModel):
    """What lets an app renew its short-lived access token without asking again. Each is
    used once and replaced; one used twice means it leaked, so the line is revoked."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    client = models.ForeignKey(
        OAuthClient, on_delete=models.CASCADE, related_name="refresh_tokens"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    access_token = models.ForeignKey(
        ApiToken, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "oauth_refresh_tokens"
