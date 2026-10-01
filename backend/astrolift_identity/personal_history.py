"""Admitted account erasure: attributed history reduction, never external stores."""

import json

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone


def _clear_owned(queryset, replacements):
    rows = queryset.exclude(Q(**replacements))
    if any(field.name == "version" for field in queryset.model._meta.fields):
        replacements = {**replacements, "version": F("version") + 1, "updated_at": timezone.now()}
    return rows.update(**replacements)


@transaction.atomic
def redact_personal_history(user):
    """Called only after the existing self/tenant/operator/owner admission.

    The database requires the stored inactive anonymous account state, fixes
    the exact history projection and compares every column.
    Legacy already-anonymous accounts can receive this newly supported cleanup
    without generating another random identity. Repeat clean calls do no writes.
    """
    from astrolift_identity.models import ApiToken, AstroliftSession
    from astrolift_operations.models import NotificationDelivery
    from auth1.models import UserInfo
    from core.schema.audit import MutationAuditLog

    if connection.vendor != "postgresql":
        raise RuntimeError("attributed user privacy reduction requires PostgreSQL")
    with connection.cursor() as cursor:
        cursor.execute("SELECT astrolift_redact_user_history(%s)", [user.pk])
        counts = cursor.fetchone()[0]
        if isinstance(counts, str):
            counts = json.loads(counts)
    counts["identity_caches"] = _clear_owned(
        UserInfo.objects.filter(internal_user_id=user.pk),
        {
            "given_name": "",
            "family_name": "",
            "nickname": "",
            "name": "",
            "picture": "",
            "email": f"anon-{user.pk}@anon-astrolift.net",
            "email_verified": False,
        },
    )
    counts["session_metadata"] = _clear_owned(
        AstroliftSession.all_objects.filter(user_id=user.pk),
        {"label": "", "last_seen_ip": None, "last_seen_agent": ""},
    )
    counts["token_metadata"] = _clear_owned(
        ApiToken.all_objects.filter(user_id=user.pk),
        {"last_used_ip": None, "last_used_agent": ""},
    )
    counts["delivery_contacts"] = _clear_owned(
        NotificationDelivery.all_objects.filter(user_id=user.pk, target_kind__in=("email", "sms")),
        {"target_address": ""},
    )
    counts["mutation_request_ips"] = _clear_owned(
        MutationAuditLog.objects.filter(user_id=user.pk), {"ip_address": None}
    )
    return counts
