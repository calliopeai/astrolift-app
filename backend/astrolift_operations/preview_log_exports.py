"""Preview artifacts retain exact source and original credential authority.

The opaque link is necessary but is not bearer authorization for preview data.
Ordinary legacy exports retain their existing capability-link contract.
"""

from __future__ import annotations

from uuid import UUID

from astrolift_identity.abac import attributes_from_request
from astrolift_identity.middleware import get_request_api_token
from astrolift_lifecycle.preview_log_access import PreviewLogAuthority
from astrolift_lifecycle.preview_targets import PreviewTarget
from core.permissions import Permission
from core.tenancy import TenantContext, get_current_tenant


def preview_export_authority(export, request):
    """Require the requester session, or the exact original API credential.

    Using a new bearer never widens an old artifact's credential ceiling. Browser
    sessions still recheck the original token row when a token created the file.
    """
    snapshot = export.source_snapshot
    if not isinstance(snapshot, dict) or snapshot.get("kind") != "preview_logs_v1":
        raise ValueError("Unproven preview export source")
    tenant = TenantContext(**snapshot["tenant"])
    target = PreviewTarget(**snapshot["target"])
    token_id = snapshot["token_id"]
    if token_id is not None and (not isinstance(token_id, int) or isinstance(token_id, bool)):
        raise ValueError("Unproven preview export authority")
    user = getattr(request, "user", None)
    current = get_current_tenant()
    token = get_request_api_token(request)
    from core.middleware.tenant import ORG_HEADER

    selected_org = request.META.get(ORG_HEADER, "")
    if selected_org and UUID(selected_org) != export.organization.guid:
        raise ValueError("Preview export access unavailable")
    if (
        user is None
        or not user.is_authenticated
        or user.pk != export.requested_by_id
        or tenant.actor_user_id != export.requested_by_id
        or tenant.organization_id != export.organization_id
        or str(export.registered_app.guid) != target.app_id
        or (current is not None and current.organization_id not in (None, tenant.organization_id))
        or (token is not None and token.pk != token_id)
    ):
        raise ValueError("Preview export access unavailable")
    authority = PreviewLogAuthority(
        tenant,
        token_id,
        attributes_from_request(request, user.pk),
        target,
        Permission.APP_LOG_EXPORT,
        snapshot["token_team_id"],
        tuple(snapshot["token_scopes"]),
    )
    authority.check()
    return authority


async def guarded_artifact_chunks(export, authority, absolute_path):
    """Check each chunk before reading; ASGI never preloads the whole artifact.

    A mid-transfer refusal closes the file and truncates the response; its
    advertised Content-Length/hash make that an incomplete download, not success.
    """
    from asgiref.sync import sync_to_async
    from django.utils import timezone

    from astrolift_operations.models import AppLogExport

    source = export.source_snapshot
    receipt = (
        export.organization_id,
        export.registered_app_id,
        export.requested_by_id,
        export.relative_path,
        export.sha256,
    )

    def read_chunk(handle):
        current = AppLogExport.objects.filter(pk=export.pk).first()
        if (
            current is None
            or current.status != AppLogExport.Status.READY
            or current.expires_at <= timezone.now()
            or current.source_snapshot != source
            or (
                current.organization_id,
                current.registered_app_id,
                current.requested_by_id,
                current.relative_path,
                current.sha256,
            )
            != receipt
        ):
            return b""
        authority.check()
        return handle.read(64 * 1024)

    handle = None
    try:
        handle = await sync_to_async(open, thread_sensitive=True)(absolute_path, "rb")
        while True:
            try:
                chunk = await sync_to_async(read_chunk, thread_sensitive=True)(handle)
            except Exception:  # source/access refusal emits no diagnostics or body data
                return
            if not chunk:
                return
            yield chunk
    finally:
        if handle is not None:
            await sync_to_async(handle.close, thread_sensitive=True)()
