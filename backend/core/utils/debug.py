import logging
from functools import wraps

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def _unauthenticated() -> JsonResponse:
    """A signed-out or expired session, in the shape GraphQL clients read.

    The web app opens its sign-in prompt on ``UNAUTHENTICATED`` and the CLI
    maps 401 to "log in again"; a bare 403 page reached neither, so an
    expired session rendered an empty app.
    """
    message = "Authentication required."
    return JsonResponse(
        {"errors": [{"message": message, "extensions": {"code": "UNAUTHENTICATED"}}]},
        status=401,
    )


def autologin(view_func):
    """
    Temporal solution for login user.
    """
    def wrapped_view(*args, **kwargs):
        if settings.DEBUG and args[0].user.is_anonymous:
            user = get_user_model().objects.filter(username=settings.DEFAULT_USER_TEST).first()
            if not user:
                return _unauthenticated()
            args[0].user = user
        elif args[0].user.is_anonymous:
            return _unauthenticated()

        result = view_func(*args, **kwargs)
        return result

    return wraps(view_func)(wrapped_view)
