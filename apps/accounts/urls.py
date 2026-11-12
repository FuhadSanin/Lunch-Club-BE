from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("me", views.MeView.as_view()),
    path("me/push-token", views.PushTokenView.as_view()),
    path("notifications", views.NotificationsView.as_view()),
    path("notifications/read", views.NotificationsReadView.as_view()),
    path("company", views.CompanyView.as_view()),
    path("company/settings", views.CompanySettingsView.as_view()),
    path("company/addresses", views.AddressesView.as_view()),
    path("company/addresses/<uuid:address_id>", views.AddressView.as_view()),
    path("company/invites", views.InvitesView.as_view()),
    path("company/invites/<uuid:invite_id>", views.InviteView.as_view()),
    path("company/members", views.MembersView.as_view()),
    path(
        "company/members/<uuid:member_id>/permissions",
        views.MemberPermissionsView.as_view(),
    ),
    path("permissions", views.PermissionsView.as_view()),
]
