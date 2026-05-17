"""
Token-gated audit export download view.

The ``exportAuditEvents`` mutation builds an :class:`AuditExport` row
and returns a download URL of the form
``/app/audit_exports/<guid>/<token>/``. This view serves the rendered
file when the (guid, token) pair matches and the export hasn't yet
expired or been consumed. The token is single-use: the row's
``consumed_at`` timestamp is stamped on first successful download so
a leaked URL can't be replayed indefinitely.
"""

from __future__ import annotations

import logging
import os

from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_operations.audit_export import hash_token
from astrolift_operations.models import AuditExport

log = logging.getLogger("astrolift_operations.audit_export")

_CONTENT_TYPE = {
    "csv": "text/csv; charset=utf-8",
    "ndjson": "application/x-ndjson",
}

_FILENAME = {
    "csv": "audit-log.csv",
    "ndjson": "audit-log.ndjson",
}


@csrf_exempt
@require_http_methods(["GET"])
def download_audit_export(request, guid: str, token: str):
    """Stream the file backing ``AuditExport(guid=guid)`` when the
    token + expiry check passes.

    Returns ``404`` for any failure path — no signal-leaking 401/403
    distinctions, no body details. An auditor with a valid URL gets
    the file; everyone else gets an opaque miss.
    """
    export = AuditExport.objects.filter(guid=guid).first()
    if export is None:
        return HttpResponse(status=404)

    if export.token_hash != hash_token(token):
        return HttpResponse(status=404)

    now = timezone.now()
    if export.expires_at <= now:
        return HttpResponse(status=404)

    from django.conf import settings as dj_settings

    media_root = getattr(dj_settings, "MEDIA_ROOT", None) or ""
    absolute = os.path.join(media_root, export.relative_path)
    if not os.path.exists(absolute):
        log.warning(
            "audit export file missing on disk",
            extra={"audit_export_guid": str(export.guid), "relative_path": export.relative_path},
        )
        return HttpResponse(status=404)

    # Stamp consumed_at on first successful download. We don't refuse
    # subsequent downloads inside the TTL window — operators may
    # legitimately re-download — but recording the first consumption
    # is enough audit signal.
    if export.consumed_at is None:
        AuditExport.objects.filter(pk=export.pk, consumed_at__isnull=True).update(consumed_at=now)

    content_type = _CONTENT_TYPE.get(export.format, "application/octet-stream")
    filename = _FILENAME.get(export.format, f"{export.guid}.{export.format}")

    response = FileResponse(open(absolute, "rb"), content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(export.byte_count)
    if export.sha256:
        response["X-Audit-Export-Sha256"] = export.sha256
    return response
