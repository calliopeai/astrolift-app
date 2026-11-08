"""Encrypted operator job admission and explicitly reviewed, read-only recovery."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.core import signing
from django.core.cache import cache
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
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

PREFIX = "tooling."
INSTALL_REVIEW_TTL = 600


def _install_review_cache_key(snapshot_id):
    try:
        identity = UUID(str(snapshot_id))
        if not identity.int:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise NvkeError("INVALID_INPUT", "Use the exact prepared installation review") from None
    return "nvke.operator.install-review.v1:" + str(identity)


def _install_review_binding(caller, row, selected):
    token = get_current_api_token()
    return {
        "actor": caller.pk,
        "token": str(token.guid) if token else None,
        "organization": row.organization_id,
        "site": str(row.site.guid),
        "site_version": row.site.version,
        "operation": str(row.guid),
        "operation_version": row.version,
        "operation_key": row.operation_key,
        "intent": row.request_digest,
        "authority": str(selected.guid),
        "authority_version": selected.version,
        "authority_digest": authority.digest(selected),
        "instance": str(selected.instance_id),
        "bundle": selected.bundle_manifest_sha256,
    }


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


def install_review(**review):
    caller, row, selected, checkpoint = reviewed_source(**review)
    if (
        row.operation_key != PREFIX + "management.install"
        or row.authority.get("tooling_effectful") is not True
        or row.status != "uncertain"
    ):
        raise NvkeError("PRECONDITION", "Review the original uncertain management installation")
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        observed = client.install_review(str(row.guid), intent=row.request_digest)
    checkpoint()
    try:
        source = observed["source"]
        native_run = str(UUID(source["nativeRunId"]))
        if (
            source
            != {
                "format": 1,
                "revision": row.authority["revision"],
                "siteId": str(row.site.guid),
                "instanceId": str(selected.instance_id),
                "bundleManifestSha256": selected.bundle_manifest_sha256,
                "id": str(row.guid),
                "intent": row.request_digest,
                "command": "management.install",
                "nativeRunId": native_run,
            }
            or observed["continuationAuthorized"] is not False
        ):
            raise ValueError
        attempt = observed["originalAttempt"]
        if (
            attempt["runId"] != native_run
            or attempt["phase"] != "uncertain"
            or type(attempt["exitCode"]) is not int
            or attempt["exitCode"] == 0
        ):
            raise ValueError
        hashes = observed["privateEvidenceSha256"]
        result = {
            "site_id": str(row.site.guid),
            "site_version": row.site.version,
            "operation_id": str(row.guid),
            "operation_version": row.version,
            "authority_id": str(selected.guid),
            "authority_version": selected.version,
            "instance_id": str(selected.instance_id),
            "bundle_manifest_sha256": selected.bundle_manifest_sha256,
            "native_revision": row.authority["revision"],
            "native_run_id": native_run,
            "review_digest": observed["reviewDigest"],
            "observed_at": datetime.fromisoformat(observed["observedAt"]),
            "original_started_at": datetime.fromisoformat(attempt["startedAt"]),
            "native_arguments_sha256": attempt["argumentsDigest"],
            "native_exit_code": attempt["exitCode"],
            "api_ready": observed["apiReady"],
            "continuation_authorized": False,
            "reviewer_sha256": observed["reviewerSha256"],
            "qualification_helper_sha256": observed["qualificationHelperSha256"],
            "evidence": {
                name + "_sha256": hashes[name]
                for name in ("guard", "started", "finished", "stdout", "stderr")
            },
            "workloads": [
                {
                    "namespace": item["namespace"],
                    "kind": item["kind"],
                    "name": item["name"],
                    "observation": item["observation"],
                    **{
                        target: str(item[key]) if item[key] is not None else None
                        for target, key in (
                            ("generation", "generation"),
                            ("observed_generation", "observedGeneration"),
                            ("desired_replicas", "desiredReplicas"),
                            ("ready_replicas", "readyReplicas"),
                            ("updated_replicas", "updatedReplicas"),
                        )
                    },
                }
                for item in observed["workloads"]
            ],
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        raise NvkeError(
            "OPERATOR_INVALID_RESPONSE", "The operator review did not confirm the original installation"
        ) from None
    checkpoint()
    snapshot_id = str(uuid4())
    payload = {
        "binding": _install_review_binding(caller, row, selected),
        "review_digest": result["review_digest"],
        "items": result["workloads"],
    }
    try:
        sealed = encrypt_at_rest(outbox.canonical(payload))
        stored = cache.set(
            _install_review_cache_key(snapshot_id),
            {"backend_kind": sealed.backend_kind, "ciphertext": sealed.backend_ref},
            timeout=INSTALL_REVIEW_TTL,
        )
        if stored is False:
            raise ValueError
    except Exception:
        raise NvkeError(
            "REVIEW_UNAVAILABLE", "Could not retain the review snapshot; the original install is unchanged"
        ) from None
    try:
        checkpoint()
    except Exception:
        try:
            cache.delete(_install_review_cache_key(snapshot_id))
        except Exception:
            pass  # Encrypted metadata expires even if best-effort cleanup is unavailable.
        raise
    result.update(
        snapshot_id=snapshot_id,
        snapshot_expires_at=timezone.now() + timedelta(seconds=INSTALL_REVIEW_TTL),
        workload_count=len(result["workloads"]),
    )
    return result


def install_workload_page(*, snapshot_id, after=None, limit=25, search="", **review):
    caller, row, selected, checkpoint = reviewed_source(**review)
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(search, str) or len(search) > 200:
        raise NvkeError("INVALID_INPUT", "Use a bounded workload search and page size")
    key = _install_review_cache_key(snapshot_id)
    try:
        sealed = cache.get(key)
        if not isinstance(sealed, dict):
            raise ValueError
        payload = json.loads(decrypt(EncryptedSecret(sealed["backend_kind"], bytes(sealed["ciphertext"]))))
        binding = _install_review_binding(caller, row, selected)
        if (
            set(payload) != {"binding", "review_digest", "items"}
            or payload["binding"] != binding
            or not isinstance(payload["items"], list)
            or len(payload["items"]) > 10000
        ):
            raise ValueError
    except Exception:
        raise NvkeError(
            "REVIEW_EXPIRED", "Load a current installation review before paging its workloads"
        ) from None
    items = [
        item
        for item in payload["items"]
        if search.casefold()
        in " ".join((item["namespace"], item["kind"], item["name"], item["observation"])).casefold()
    ]
    position = 0
    cursor_binding = {
        "snapshot": str(UUID(str(snapshot_id))),
        "binding": hashlib.sha256(outbox.canonical(binding)).hexdigest(),
        "digest": payload["review_digest"],
        "limit": limit,
        "search": search,
    }
    salt = "nvke.operator.install.workloads.v1"
    if after is not None:
        try:
            if not isinstance(after, str) or len(after) > 8192:
                raise ValueError
            decoded = signing.loads(after, salt=salt, max_age=INSTALL_REVIEW_TTL)
            position = decoded.pop("position")
            if decoded != cursor_binding or type(position) is not int or not 0 <= position <= len(items):
                raise ValueError
        except (signing.BadSignature, ValueError, TypeError, KeyError, AttributeError):
            raise NvkeError(
                "INVALID_CURSOR", "Use a cursor from this exact prepared workload review"
            ) from None
    end = position + limit
    checkpoint()
    return {
        "snapshot_id": str(UUID(str(snapshot_id))),
        "operation_id": str(row.guid),
        "operation_version": row.version,
        "review_digest": payload["review_digest"],
        "items": items[position:end],
        "total_count": len(items),
        "next_cursor": signing.dumps({**cursor_binding, "position": end}, salt=salt)
        if end < len(items)
        else None,
    }


def artifact_page(*, after=None, limit=50, search="", **review):
    caller, row, selected, checkpoint = reviewed_source(**review)
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(search, str) or len(search) > 200:
        raise NvkeError("INVALID_INPUT", "Use a bounded artifact search and page size")
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        try:
            peer = client.observe(
                str(row.guid), command=command_key(row.operation_key), intent=row.request_digest
            )
        except NvkeError as error:
            if error.code != "OPERATOR_JOB_NOT_FOUND":
                raise
            peer = None
    checkpoint()
    if peer is not None and peer["effectful"] != row.authority["tooling_effectful"]:
        raise NvkeError("OPERATOR_INVALID_RESPONSE", "The original operator effect classification changed")
    if (
        peer is not None
        and peer.get("nativeExitCode") is not None
        and not -(2**31) <= peer["nativeExitCode"] < 2**31
    ):
        raise NvkeError("OPERATOR_INVALID_RESPONSE", "The operator native exit code is invalid")
    metadata = {
        "site_id": str(row.site.guid),
        "site_version": row.site.version,
        "operation_id": str(row.guid),
        "operation_version": row.version,
        "authority_id": str(selected.guid),
        "authority_version": selected.version,
        "instance_id": str(selected.instance_id),
        "bundle_manifest_sha256": selected.bundle_manifest_sha256,
        "native_revision": row.authority["revision"],
        "command": command_key(row.operation_key),
        "found": peer is not None,
        "state": peer["state"] if peer else "not_found",
        "effectful": row.authority["tooling_effectful"],
        "cancellation_requested": peer["cancellationRequested"] if peer else False,
        "native_exit_code": peer.get("nativeExitCode") if peer else None,
        "error_code": peer.get("errorCode") if peer else None,
        "reconciled_from_native_receipt": peer.get("reconciledFromNativeReceipt", False) if peer else False,
    }
    artifacts = sorted(peer["artifacts"], key=lambda item: item["name"]) if peer else []
    digest = hashlib.sha256(outbox.canonical({**metadata, "artifacts": artifacts})).hexdigest()
    scope = [caller.pk, row.organization_id, metadata, digest, search, limit]
    salt = "nvke.operator.artifacts.v1"
    offset = 0
    if after is not None:
        try:
            if not isinstance(after, str) or len(after) > 8192:
                raise ValueError
            cursor = signing.loads(after, salt=salt, max_age=3600)
            offset = cursor["offset"]
            if cursor["scope"] != scope or type(offset) is not int or offset < 0:
                raise ValueError
        except Exception:
            raise NvkeError("INVALID_CURSOR", "Use a cursor from this reviewed operator receipt") from None
    values = [item for item in artifacts if search.casefold() in item["name"].casefold()]
    if offset > len(values):
        raise NvkeError("INVALID_CURSOR", "The operator artifact cursor is outside this receipt")
    items = values[offset : offset + limit]
    next_offset = offset + len(items)
    next_cursor = (
        signing.dumps({"scope": scope, "offset": next_offset}, salt=salt, compress=True)
        if next_offset < len(values)
        else None
    )
    return {
        **metadata,
        "receipt_sha256": digest,
        "items": items,
        "next_cursor": next_cursor,
        "total_count": len(values),
    }


def reconcile(**review):
    return _reviewed_job_action(review, stop=False)


def stop(**review):
    return _reviewed_job_action(review, stop=True)


def _reviewed_job_action(review, *, stop):
    from astrolift_clusters.nvke_operator_runtime import record_peer
    from astrolift_clusters.nvke_runtime import _state, operation_lease_key

    caller, row, selected, checkpoint = reviewed_source(**review)
    if row.status != "uncertain":
        raise NvkeError("PRECONDITION", "Only an uncertain operator intent needs reviewed recovery")
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
                if stop:
                    peer = client.stop_or_fence(
                        str(row.guid),
                        command=command_key(row.operation_key),
                        intent=row.request_digest,
                        effectful=row.authority["tooling_effectful"],
                    )
                else:
                    peer = client.observe(
                        str(row.guid), command=command_key(row.operation_key), intent=row.request_digest
                    )
            except NvkeError as error:
                if stop or error.code != "OPERATOR_JOB_NOT_FOUND":
                    raise
                # Absence cannot fence a delayed original PUT still in flight.
                _state(
                    row,
                    "uncertain",
                    "operator_receipt_missing",
                    progress=progress,
                    error=NvkeError(
                        "OPERATOR_RECEIPT_NOT_FOUND",
                        "The original receipt is absent; a delayed admission remains possible and infrastructure intent stays fenced",
                    ),
                    audit_actor_id=caller.pk,
                )
            else:
                record_peer(row, progress, peer, audit_actor_id=caller.pk, observe_only=True)
        row.refresh_from_db()
    return row
