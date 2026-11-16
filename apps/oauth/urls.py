from django.urls import path

from apps.oauth import views

# At the site root: /.well-known/... and /oauth/... (config/urls.py)
root = [
    path(".well-known/oauth-authorization-server", views.MetadataView.as_view()),
    path("oauth/register", views.RegisterView.as_view()),
    path("oauth/authorize", views.AuthorizeView.as_view()),
    path("oauth/token", views.TokenView.as_view()),
    path("oauth/revoke", views.RevokeView.as_view()),
]

# Under /api/: the web app's consent screen
api = [
    path("oauth/requests/<uuid:request_id>", views.RequestView.as_view()),
    path("oauth/requests/<uuid:request_id>/approve", views.ApproveView.as_view()),
    path("oauth/requests/<uuid:request_id>/deny", views.DenyView.as_view()),
]
