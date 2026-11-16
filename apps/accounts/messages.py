"""What people are told when something isn't allowed. Read on screen, so in the app's
words, not the code's."""

from apps.accounts.models import Permission

# What each permission lets someone do, to finish the sentence "Your role doesn't let
# you ...". Said in the app's words, because people read this on screen.
_WHAT = {
    Permission.SESSION_CREATE: "start a food party",
    Permission.SESSION_READ: "see food parties",
    Permission.SESSION_UPDATE: (
        "run a food party (lock picks, tick things off, close it)"
    ),
    Permission.SESSION_DELETE: "call off a food party",
    Permission.PICK_CREATE: "pick dishes",
    Permission.PICK_UPDATE: "change picks or skip a food party",
    Permission.PICK_DELETE: "remove picks",
    Permission.REVIEW_CREATE: "confirm the cart",
    Permission.ORDER_UPDATE: "update orders",
    Permission.RESTAURANT_READ: "browse restaurants and menus",
    Permission.MEMBER_CREATE: "invite people",
    Permission.MEMBER_READ: "see the team",
    Permission.MEMBER_UPDATE: "change roles",
    Permission.MEMBER_DELETE: "withdraw invites",
    Permission.COMPANY_UPDATE: "change the company's settings",
}


def not_allowed(permission: str) -> str:
    """What to tell someone whose roles lack `permission`: what they can't do, who can
    fix it, and the permission's code for whoever is asked."""
    what = _WHAT.get(Permission(permission), "do this")
    return (
        f"Your role doesn't let you {what}. Ask a host to change your role. "
        f"(It needs {permission}.)"
    )
