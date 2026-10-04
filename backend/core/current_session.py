"""Read-only validation of the current persisted browser authentication snapshot.

Call again after admission waits. This does not lock the session or promise that
logout cannot race a subsequent side effect.
"""

from types import SimpleNamespace

from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db.models import Q
from django.utils import timezone

from core.permissions import PermissionDenied


class _ReadOnlyAuthSession(SessionStore):
    key_salt = SessionStore().key_salt

    def cycle_key(self):
        # get_user may accept a configured fallback signing hash; no DB writes.
        pass

    def flush(self):
        self._session_cache = {}
        self._session_key = None


def fresh_authenticated_session(request, *, actor_user_id, permission):
    """Validate actual DB state rather than request-cached user/session data."""
    from astrolift_identity.models import AstroliftSession

    def refuse():
        # Response tracking must not revive the cached authentication this
        # read-only check has just refused. This does not modify the session.
        request._astrolift_session_unavailable = True
        raise PermissionDenied(permission, None, "Current authenticated session is unavailable.")

    if getattr(getattr(request, "user", None), "pk", None) != actor_user_id:
        refuse()
    key = getattr(getattr(request, "session", None), "session_key", None)
    now = timezone.now()
    stored = Session.objects.filter(session_key=key, expire_date__gt=now).first() if key else None
    if stored is None:
        refuse()
    store = _ReadOnlyAuthSession(key)
    store._session_cache = stored.get_decoded()
    user = get_user(SimpleNamespace(session=store))
    if not user.is_authenticated or user.pk != actor_user_id or store.session_key != key:
        refuse()
    invalid = (
        Q(deleted_at__isnull=False)
        | Q(revoked_at__isnull=False)
        | Q(expires_at__lte=now)
        | ~Q(user_id=actor_user_id)
    )
    if AstroliftSession.all_objects.filter(session_key=key).filter(invalid).exists():
        refuse()
    return store
