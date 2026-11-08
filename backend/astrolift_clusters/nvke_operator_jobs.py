"""Encrypted operator job admission and explicitly reviewed, read-only recovery."""

import hashlib
import hmac
import json
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from nvke.client import NvkeError
from nvke.contract import upstream_revision
from nvke.operator_contract import CATALOG, MAX_REQUEST_BYTES, ContractError, effectful, validate

from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeOperation, NvkeOperatorAuthority
from astrolift_identity.api_tokens import get_current_api_token
from core.secrets import encrypt_at_rest

PREFIX = "tooling."


def command_key(key):
    if not isinstance(key, str) or not key.startswith(PREFIX) or key[len(PREFIX) :] not in CATALOG:
        raise NvkeError("INVALID_INPUT", "Choose a pinned operator command")
    return key[len(PREFIX) :]


def reserve(
    *,
    site_id,
    expected_actor_id,
    expected_site_version,
    expected_authority_version,
    request_id,
    request_text,
    acknowledge_infrastructure_change=False,
    timeout_seconds=7200,
):
    org, caller = authority.actor(expected_actor_id)
    try:
        identity = UUID(str(request_id))
        if (
            not identity.int
            or not isinstance(request_text, str)
            or len(request_text.encode()) > MAX_REQUEST_BYTES
        ):
            raise ValueError
        request = validate(json.loads(request_text), str(site_id))
    except (ValueError, TypeError, UnicodeError, ContractError):
        raise NvkeError(
            "INVALID_INPUT", "Review one bounded pinned operator request and request UUID"
        ) from None
    if (
        type(timeout_seconds) is not int
        or not 30 <= timeout_seconds <= 86400
        or type(acknowledge_infrastructure_change) is not bool
    ):
        raise NvkeError(
            "INVALID_INPUT", "Use a bounded operator observation deadline and explicit acknowledgement"
        )
    mutates = effectful(request)
    if mutates and acknowledge_infrastructure_change is not True:
        raise NvkeError("PRECONDITION", "Review the site/agent and acknowledge this infrastructure change")
    token = get_current_api_token()
    reviewed = {
        "actor": caller.pk,
        "token_id": str(token.guid) if token else None,
        "request_id": str(identity),
        "site": str(site_id),
        "site_version": expected_site_version,
        "authority_version": expected_authority_version,
        "request": request,
        "acknowledged": acknowledge_infrastructure_change,
        "timeout": timeout_seconds,
    }
    digest = hmac.new(
        str(settings.SECRET_KEY).encode(), outbox.canonical(reviewed), hashlib.sha256
    ).hexdigest()
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        existing = (
            NvkeOperation.all_objects.select_for_update()
            .filter(site=site, actor=caller, request_id=identity)
            .first()
        )
        if existing is not None:
            if existing.deleted_at is not None or not hmac.compare_digest(existing.request_digest, digest):
                raise NvkeError("CONFLICT", "This request ID already identifies another operator intent")
            if not existing.terminal:
                transaction.on_commit(lambda: outbox.dispatch(existing.pk))
            return existing
        sites._version(site, expected_site_version)
        if not site.is_active or site.upstream_revision != upstream_revision():
            raise NvkeError("SOURCE_CHANGED", "Operator site authority changed")
        selected = (
            NvkeOperatorAuthority.objects.select_related("site")
            .filter(site=site, is_enabled=True, verified_at__isnull=False)
            .first()
        )
        if selected is None:
            raise NvkeError(
                "SURFACE_UNAVAILABLE", "Configure and verify this site's explicit operator authority"
            )
        sites._version(selected, expected_authority_version)
        if mutates:
            active = NvkeOperation.objects.filter(site=site, authority__tooling_effectful=True).exclude(
                status__in=["succeeded", "failed", "cancelled"]
            )
            if active.exists():
                raise NvkeError(
                    "RESOURCE_BUSY",
                    "Review the existing operator intent before another infrastructure change",
                )
            if (
                request["command"] == "bootstrap.site"
                and NvkeOperation.objects.filter(
                    site=site,
                    operation_key=PREFIX + "bootstrap.site",
                    status="succeeded",
                    authority__tooling_effectful=True,
                ).exists()
            ):
                raise NvkeError(
                    "PRECONDITION", "This site already has a successful bootstrap; use management checks"
                )
        token = get_current_api_token()
        sealed = encrypt_at_rest(outbox.canonical(request))
        row = NvkeOperation.objects.create(
            site=site,
            organization=org,
            actor=caller,
            request_id=identity,
            request_digest=digest,
            operation_key=PREFIX + request["command"],
            expected_site_version=site.version,
            authority={
                "operator_authority_id": str(selected.guid),
                "operator_authority_digest": authority.digest(selected),
                "operator_instance_id": str(selected.instance_id),
                "operator_bundle_sha256": selected.bundle_manifest_sha256,
                "tooling_effectful": mutates,
                "token_id": str(token.guid) if token else None,
                "revision": upstream_revision(),
                "organization_namespace": site.organization_namespace,
            },
            input_backend_kind=sealed.backend_kind,
            input_ciphertext=sealed.backend_ref,
            deadline=timezone.now() + timedelta(seconds=timeout_seconds),
            created_by=caller,
            updated_by=caller,
        )
        transaction.on_commit(lambda: outbox.dispatch(row.pk))
        return row


def reviewed_source(
    *,
    operation_id,
    expected_actor_id,
    expected_site_version,
    expected_authority_version,
    expected_operation_version,
):
    org, caller = authority.actor(expected_actor_id)
    row = outbox.read(operation_id)
    command_key(row.operation_key)
    site = sites.site_row(row.site.guid, org)
    sites._version(site, expected_site_version)
    sites._version(row, expected_operation_version)
    selected = (
        NvkeOperatorAuthority.objects.select_related("site")
        .filter(site=site, is_enabled=True, verified_at__isnull=False)
        .first()
    )
    if selected is None:
        raise NvkeError("SURFACE_UNAVAILABLE", "Review current operator authority first")
    sites._version(selected, expected_authority_version)
    if (
        not site.is_active
        or site.upstream_revision != row.authority.get("revision")
        or str(selected.instance_id) != row.authority.get("operator_instance_id")
        or selected.bundle_manifest_sha256 != row.authority.get("operator_bundle_sha256")
    ):
        raise NvkeError("SOURCE_CHANGED", "Original operator job belongs to a different site/agent identity")
    original = authority.digest(selected)

    def checkpoint():
        current_org, _caller = authority.actor(expected_actor_id)
        current_site = sites.site_row(site.guid, current_org)
        current = (
            NvkeOperatorAuthority.objects.select_related("site")
            .filter(pk=selected.pk, site=current_site, is_enabled=True, verified_at__isnull=False)
            .first()
        )
        observed = NvkeOperation.objects.filter(
            pk=row.pk, site=current_site, version=expected_operation_version
        ).first()
        if (
            not current_site.is_active
            or current_site.version != expected_site_version
            or current is None
            or observed is None
            or not hmac.compare_digest(authority.digest(current), original)
        ):
            raise NvkeError("SOURCE_CHANGED", "Reviewed operator source or authority changed")

    checkpoint()
    return caller, row, selected, checkpoint


def artifact(
    *,
    operation_id,
    expected_actor_id,
    expected_site_version,
    expected_authority_version,
    expected_operation_version,
    name,
    offset="0",
    limit=262144,
):
    _caller, row, selected, checkpoint = reviewed_source(
        operation_id=operation_id,
        expected_actor_id=expected_actor_id,
        expected_site_version=expected_site_version,
        expected_authority_version=expected_authority_version,
        expected_operation_version=expected_operation_version,
    )
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        result = client.artifact(
            str(row.guid),
            command=command_key(row.operation_key),
            intent=row.request_digest,
            name=name,
            offset=offset,
            limit=limit,
        )
    checkpoint()
    return result


def reconcile(**review):
    from astrolift_clusters.nvke_operator_runtime import record_peer
    from astrolift_clusters.nvke_runtime import _state, operation_lease_key

    caller, row, selected, checkpoint = reviewed_source(**review)
    if row.status != "uncertain":
        raise NvkeError("PRECONDITION", "Only an uncertain operator intent needs reviewed reconciliation")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [operation_lease_key(row.guid)])
            if not cursor.fetchone()[0]:
                raise NvkeError("RESOURCE_BUSY", "The original operator observer is still active")
        row = NvkeOperation.objects.select_for_update().select_related("site").get(pk=row.pk)
        checkpoint()
        progress = outbox.decode_result(row)
        with authority.open_agent(selected, checkpoint=checkpoint) as client:
            try:
                peer = client.observe(
                    str(row.guid), command=command_key(row.operation_key), intent=row.request_digest
                )
            except NvkeError as error:
                if error.code != "OPERATOR_JOB_NOT_FOUND":
                    raise
                _state(
                    row,
                    "failed",
                    "operator_intent_not_admitted",
                    progress=progress,
                    error=NvkeError(
                        "OPERATOR_NOT_ADMITTED",
                        "The original bound agent confirms that this job was not admitted",
                    ),
                    audit_actor_id=caller.pk,
                )
            else:
                record_peer(row, progress, peer, audit_actor_id=caller.pk, observe_only=True)
        row.refresh_from_db()
    return row
