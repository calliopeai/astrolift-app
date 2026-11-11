"""Durable private preparation intents, separate from native execution admission."""

import copy
import hashlib
import hmac
import json
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from nvke import _operator_install_plan as protocol
from nvke.client import NvkeError
from nvke.operator_contract import ContractError

from astrolift_clusters import nvke_artifact_identity as artifact_identity
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters import nvke_operator_jobs as jobs
from astrolift_clusters.models import (
    NvkeInstallPlan,
    NvkeOperation,
)
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

CURSOR_SALT = "nvke.operator.install-plan.page.v1"


def _source(review):
    caller, original, selected, checkpoint = jobs.reviewed_source(**review)
    if (
        original.operation_key != jobs.PREFIX + "management.install"
        or original.authority.get("tooling_effectful") is not True
        or original.status != "uncertain"
    ):
        raise NvkeError("PRECONDITION", "Review the original uncertain management installation")
    return caller, original, selected, checkpoint


def _identity(plan_id):
    try:
        value = UUID(str(plan_id))
        if not value.int:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError):
        raise NvkeError("INVALID_INPUT", "Use the exact retained install plan identity") from None


def _binding(caller, original, selected):
    return jobs._install_review_binding(caller, original, selected)


def _retained(plan_id, caller, original, selected, *, locked=False):
    query = NvkeInstallPlan.all_objects
    if locked:
        query = query.select_for_update()
    row = query.filter(guid=_identity(plan_id), original_operation=original, deleted_at__isnull=True).first()
    if row is None:
        raise NvkeError("NOT_FOUND", "Install plan not found")
    current = _binding(caller, original, selected)
    retained = {**row.source_binding, "actor": current["actor"], "token": current["token"]}
    if retained != current:
        raise NvkeError("SOURCE_CHANGED", "The retained plan belongs to another reviewed source")
    return row


def _summary(data, original, selected, plan_id):
    try:
        # Revalidate at the application boundary even when a custom agent adapter is installed.
        exit_code = data["plan"]["source"]["nativeExitCode"]
        return protocol.summary(
            data,
            site=str(original.site.guid),
            instance=str(selected.instance_id),
            bundle=selected.bundle_manifest_sha256,
            job=str(original.guid),
            plan_id=str(plan_id),
            exit_code=exit_code,
        )
    except (ContractError, KeyError, ValueError, TypeError, OverflowError):
        raise NvkeError(
            "OPERATOR_INVALID_RESPONSE", "The agent did not confirm the reviewed install plan"
        ) from None


def _public(row, original, selected):
    return {
        "id": str(row.guid),
        "version": row.version,
        "status": row.status,
        "operation_id": str(original.guid),
        "operation_version": original.version,
        "site_id": str(original.site.guid),
        "site_version": original.site.version,
        "authority_id": str(selected.guid),
        "authority_version": selected.version,
        "metadata": row.summary,
    }


def prepare(*, body, **review):
    caller, original, selected, checkpoint = _source(review)
    try:
        body = protocol.preparation_request(body, site=str(original.site.guid), job_id=str(original.guid))
    except (ContractError, KeyError, ValueError, TypeError, OverflowError):
        raise NvkeError("INVALID_INPUT", "Use a bounded reviewed management-install plan request") from None
    plan_id = UUID(body["planId"])
    binding = _binding(caller, original, selected)
    digest = hmac.new(
        str(settings.SECRET_KEY).encode(),
        b"nvke.install-plan.prepare.v1\0" + outbox.canonical({"binding": binding, "body": body}),
        hashlib.sha256,
    ).hexdigest()
    with transaction.atomic():
        # Serializing on the original also prevents a competing operation transition at reservation.
        NvkeOperation.objects.select_for_update().get(pk=original.pk)
        checkpoint()
        artifact_identity.lock(plan_id)
        checkpoint()
        row = NvkeInstallPlan.all_objects.select_for_update().filter(guid=plan_id).first()
        if row is not None:
            if (
                row.deleted_at is not None
                or row.original_operation_id != original.pk
                or row.actor_id != caller.pk
                or row.source_binding != binding
                or not hmac.compare_digest(row.request_digest, digest)
            ):
                raise NvkeError(
                    "CONFLICT", "This plan identity already identifies another preparation intent"
                )
        else:
            if artifact_identity.reserved(plan_id, excluding=NvkeInstallPlan):
                raise NvkeError("CONFLICT", "This identity already identifies an operator operation")
            sealed = encrypt_at_rest(outbox.canonical(body))
            try:
                with transaction.atomic():
                    row = NvkeInstallPlan.objects.create(
                        guid=plan_id,
                        original_operation=original,
                        actor=caller,
                        source_binding=binding,
                        request_digest=digest,
                        input_backend_kind=sealed.backend_kind,
                        input_ciphertext=sealed.backend_ref,
                        created_by=caller,
                        updated_by=caller,
                    )
            except IntegrityError:
                raise NvkeError(
                    "CONFLICT", "This plan identity already identifies another preparation intent"
                ) from None
    checkpoint()
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.prepare_install_plan(str(original.guid), intent=original.request_digest, body=body)
    result = _summary(data, original, selected, plan_id)
    checkpoint()
    return _publish(row.guid, result, review)


def _publish(plan_id, result, review):
    caller, original, selected, checkpoint = _source(review)
    with transaction.atomic():
        NvkeOperation.objects.select_for_update().get(pk=original.pk)
        checkpoint()
        row = _retained(plan_id, caller, original, selected, locked=True)
        if row.status == "prepared":
            if row.summary != result:
                raise NvkeError("SOURCE_CHANGED", "The retained immutable plan summary changed")
        else:
            row.summary, row.status, row.updated_by = result, "prepared", caller
            row.save(update_fields=["summary", "status", "updated_by", "updated_at", "version"])
        checkpoint()
        return _public(row, original, selected)


def read(*, plan_id, **review):
    caller, original, selected, checkpoint = _source(review)
    row = _retained(plan_id, caller, original, selected)
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.install_plan(str(original.guid), intent=original.request_digest, plan_id=str(row.guid))
    result = _summary(data, original, selected, row.guid)
    checkpoint()
    # A lost preparation reply can be recovered by a GET, without rendering again.
    return _publish(row.guid, result, review)


def recover_preparation(*, plan_id, **review):
    caller, original, selected, checkpoint = _source(review)
    row = _retained(plan_id, caller, original, selected)
    if row.source_binding != _binding(caller, original, selected):
        raise NvkeError(
            "PRECONDITION", "Only the original preparation caller and token can repeat its private intent"
        )
    try:
        body = json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))))
    except Exception:
        raise NvkeError(
            "PREPARATION_UNAVAILABLE", "The retained private preparation intent is unavailable"
        ) from None
    checkpoint()
    return prepare(body=body, **review)


def page(*, plan_id, plan_digest, view, after=None, limit=25, search="", **review):
    caller, original, selected, checkpoint = _source(review)
    if (
        not isinstance(view, str)
        or view not in {"items", "workloads"}
        or type(limit) is not int
        or not 1 <= limit <= 100
        or not isinstance(search, str)
        or len(search) > 200
        or not isinstance(plan_digest, str)
        or not protocol.SHA.fullmatch(plan_digest)
    ):
        raise NvkeError("INVALID_INPUT", "Use a bounded source-bound install plan page request")
    row = _retained(plan_id, caller, original, selected)
    if row.status != "prepared" or row.summary.get("plan", {}).get("planDigest") != plan_digest:
        raise NvkeError("SOURCE_CHANGED", "Read the prepared plan before paging its contents")

    def plan_checkpoint():
        checkpoint()
        current = _retained(row.guid, caller, original, selected)
        if current.version != row.version or current.status != row.status or current.summary != row.summary:
            raise NvkeError("SOURCE_CHANGED", "The reviewed plan changed during observation")

    plan_checkpoint()
    binding = {
        "plan": str(row.guid),
        "digest": plan_digest,
        "source": hashlib.sha256(outbox.canonical(_binding(caller, original, selected))).hexdigest(),
        "view": view,
        "limit": limit,
        "search": search,
    }
    native_cursor = None
    if after is not None:
        try:
            if not isinstance(after, str) or not 1 <= len(after) <= 16384:
                raise ValueError
            decoded = signing.loads(after, salt=CURSOR_SALT, max_age=3600)
            native_cursor = decoded.pop("native")
            if (
                decoded != binding
                or not isinstance(native_cursor, str)
                or not 1 <= len(native_cursor) <= 8192
            ):
                raise ValueError
        except Exception:
            raise NvkeError("INVALID_CURSOR", "Use the exact current plan page cursor") from None
    with authority.open_agent(selected, checkpoint=plan_checkpoint) as client:
        data = client.install_plan_page(
            str(original.guid),
            intent=original.request_digest,
            plan_id=str(row.guid),
            plan_digest=plan_digest,
            view=view,
            after=native_cursor,
            limit=limit,
            search=search,
        )
    plan_checkpoint()
    try:
        wire = copy.deepcopy(data)
        if view == "workloads":
            for item in wire["items"]:
                for key in protocol.COUNTERS:
                    if item[key] is not None:
                        item[key] = int(protocol.decimal(item[key]))
        data = protocol.page(
            wire, metadata=row.summary, view=view, limit=limit, search=search, initial=after is None
        )
    except (ContractError, KeyError, ValueError, TypeError, OverflowError):
        raise NvkeError(
            "OPERATOR_INVALID_RESPONSE", "The agent did not confirm the retained plan page"
        ) from None
    plan_checkpoint()
    cursor = data["nextCursor"]
    return {
        "plan_id": str(row.guid),
        "plan_digest": plan_digest,
        "view": view,
        "items": data["items"],
        "total_count": data["totalCount"],
        "next_cursor": signing.dumps({**binding, "native": cursor}, salt=CURSOR_SALT, compress=True)
        if cursor
        else None,
    }
