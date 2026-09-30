"""Tenant-bound stable keyset windows over the append-only deployment run log."""

import json

from django.core import signing
from django.db.models import Max

from astrolift_lifecycle.models import Deployment, DeploymentLog
from astrolift_lifecycle.visibility import historical_deployment_rows
from core.tenancy import get_current_tenant

_SALT = "deployment-run-log-2176"


def deployment_for_log(deployment_id: str):
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return None
    return (
        historical_deployment_rows(Deployment.objects.all())
        .filter(guid=deployment_id, registered_app__organization_id=tenant.organization_id)
        .first()
    )


def log_window(deployment, cursor: str | None, limit: int):
    org_id = deployment.registered_app.organization_id
    rows = DeploymentLog.objects.filter(deployment_id=deployment.pk)
    if cursor:
        try:
            payload = signing.loads(cursor, salt=_SALT, max_age=86400)
            if (
                payload["deployment"] != str(deployment.guid)
                or payload["org"] != org_id
                or type(payload["before"]) is not int
                or type(payload["snapshot"]) is not int
                or not 0 < payload["before"] <= payload["snapshot"]
            ):
                raise ValueError
            snapshot, before = payload["snapshot"], payload["before"]
        except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid deployment run-log cursor") from exc
    else:
        snapshot = rows.aggregate(last=Max("pk"))["last"] or 0
        before = snapshot + 1
    limit = max(1, min(200, limit))
    found = list(rows.filter(pk__lte=snapshot, pk__lt=before).order_by("-pk")[: limit + 1])
    more = len(found) > limit
    items = list(reversed(found[:limit]))
    next_cursor = (
        signing.dumps(
            {"deployment": str(deployment.guid), "org": org_id, "snapshot": snapshot, "before": items[0].pk},
            salt=_SALT,
        )
        if more
        else None
    )
    return items, next_cursor, more, limit


def download_text(deployment):
    rows = DeploymentLog.objects.filter(deployment_id=deployment.pk)
    snapshot = rows.aggregate(last=Max("pk"))["last"] or 0
    lines = []
    for entry in rows.filter(pk__lte=snapshot).order_by("pk").iterator(chunk_size=500):
        label = "/".join(filter(None, (entry.phase, entry.event))) or entry.status
        lines.append(f"{entry.occurred_at.isoformat()} [{label}] {entry.message}")
        if entry.detail:
            lines.append(json.dumps(entry.detail, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + ("\n" if lines else "")
