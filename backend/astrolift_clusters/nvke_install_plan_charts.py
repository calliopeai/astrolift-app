"""Durable chart intents and current-admin recovery, separate from native admission."""

import hashlib
import hmac
import json
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from nvke import _operator_install_plan_charts as protocol
from nvke.client import NvkeError
from nvke.operator_contract import ContractError

from astrolift_clusters import nvke_artifact_identity as artifact_identity
from astrolift_clusters import nvke_install_plans as plans
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters.models import (
    NvkeInstallPlan,
    NvkeInstallPlanCharts,
    NvkeInstallPlanContext,
    NvkeInstallPlanTree,
    NvkeOperation,
)
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest


def _source(review, plan_id, expected_plan_version, plan_digest):
    caller, original, selected, checkpoint = plans._source(review)
    if (
        type(expected_plan_version) is not int
        or expected_plan_version < 0
        or not isinstance(plan_digest, str)
        or not protocol.SHA.fullmatch(plan_digest)
    ):
        raise NvkeError("INVALID_INPUT", "Review the exact prepared plan version and digest")
    parent = plans._retained(plan_id, caller, original, selected)
    if (
        parent.version != expected_plan_version
        or parent.status != "prepared"
        or parent.summary.get("plan", {}).get("planDigest") != plan_digest
    ):
        raise NvkeError("SOURCE_CHANGED", "The reviewed prepared plan changed")
    plans._summary(parent.summary, original, selected, parent.guid)
    binding = {
        **plans._binding(caller, original, selected),
        "plan": str(parent.guid),
        "plan_version": parent.version,
        "plan_digest": plan_digest,
        "plan_summary_sha256": hashlib.sha256(outbox.canonical(parent.summary)).hexdigest(),
    }

    def current():
        checkpoint()
        value = plans._retained(parent.guid, caller, original, selected)
        if (
            value.version != parent.version
            or value.status != parent.status
            or value.summary != parent.summary
        ):
            raise NvkeError("SOURCE_CHANGED", "The reviewed prepared plan changed")

    current()
    return caller, original, selected, parent, binding, current


def _retained(artifact_id, parent, binding, *, locked=False):
    query = NvkeInstallPlanCharts.all_objects
    if locked:
        query = query.select_for_update()
    row = query.filter(guid=plans._identity(artifact_id), plan=parent, deleted_at__isnull=True).first()
    if row is None:
        raise NvkeError("NOT_FOUND", "Chart artifact preparation not found")
    observed = {**row.source_binding, "actor": binding["actor"], "token": binding["token"]}
    if observed != binding:
        raise NvkeError("SOURCE_CHANGED", "The chart artifacts belong to another reviewed source")
    return row


def _digest(binding, body):
    return hmac.new(
        str(settings.SECRET_KEY).encode(),
        b"nvke.install-plan.charts.prepare.v1\0" + outbox.canonical({"binding": binding, "body": body}),
        hashlib.sha256,
    ).hexdigest()


def _body(row, parent):
    try:
        body = json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))))
        body = protocol.request(body, job_id=str(parent.original_operation.guid), read_only=False)
        if (
            row.actor_id != row.source_binding["actor"]
            or body["artifactId"] != str(row.guid)
            or body["planId"] != str(parent.guid)
            or body["planDigest"] != row.source_binding["plan_digest"]
            or not hmac.compare_digest(_digest(row.source_binding, body), row.request_digest)
        ):
            raise ValueError
        return body
    except Exception:
        raise NvkeError(
            "PREPARATION_UNAVAILABLE", "The retained private chart preparation intent is unavailable"
        ) from None


def _summary(data, row, parent):
    try:
        # A custom adapter must satisfy the same boundary as the mTLS SDK.
        body = _body(row, parent)
        return protocol.summary(data, body=body, item_count=parent.summary["itemCount"])
    except Exception:
        raise NvkeError(
            "OPERATOR_INVALID_RESPONSE", "The agent did not confirm the retained chart artifacts"
        ) from None


def _public(row, parent, original, selected):
    return {
        "id": str(row.guid),
        "version": row.version,
        "status": row.status,
        "plan_id": str(parent.guid),
        "plan_version": parent.version,
        "operation_id": str(original.guid),
        "operation_version": original.version,
        "site_id": str(original.site.guid),
        "site_version": original.site.version,
        "authority_id": str(selected.guid),
        "authority_version": selected.version,
        "metadata": row.summary,
    }


def prepare(*, body, plan_id, expected_plan_version, plan_digest, **review):
    caller, original, selected, parent, binding, checkpoint = _source(
        review, plan_id, expected_plan_version, plan_digest
    )
    try:
        body = protocol.request(body, job_id=str(original.guid), read_only=False)
        if body["planId"] != str(parent.guid) or body["planDigest"] != plan_digest:
            raise ValueError
    except (ContractError, ValueError, KeyError, TypeError, OverflowError):
        raise NvkeError("INVALID_INPUT", "Use a bounded chart request for this exact prepared plan") from None
    artifact_id = UUID(body["artifactId"])
    digest = _digest(binding, body)
    with transaction.atomic():
        NvkeOperation.objects.select_for_update().get(pk=original.pk)
        NvkeInstallPlan.objects.select_for_update().get(pk=parent.pk)
        checkpoint()
        artifact_identity.lock(artifact_id)
        checkpoint()
        row = NvkeInstallPlanCharts.all_objects.select_for_update().filter(guid=artifact_id).first()
        if row is not None:
            if (
                row.deleted_at is not None
                or row.plan_id != parent.pk
                or row.actor_id != caller.pk
                or row.source_binding != binding
                or not hmac.compare_digest(row.request_digest, digest)
            ):
                raise NvkeError(
                    "CONFLICT", "This artifact identity already identifies another preparation intent"
                )
        else:
            if (
                NvkeOperation.all_objects.filter(guid=artifact_id).exists()
                or NvkeInstallPlan.all_objects.filter(guid=artifact_id).exists()
                or NvkeInstallPlanTree.all_objects.filter(guid=artifact_id).exists()
                or NvkeInstallPlanContext.all_objects.filter(guid=artifact_id).exists()
            ):
                raise NvkeError("CONFLICT", "This identity already identifies another operation or plan")
            sealed = encrypt_at_rest(outbox.canonical(body))
            try:
                with transaction.atomic():
                    row = NvkeInstallPlanCharts.objects.create(
                        guid=artifact_id,
                        plan=parent,
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
                    "CONFLICT", "This artifact identity already identifies another preparation intent"
                ) from None
    checkpoint()
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.prepare_install_plan_charts(
            str(original.guid), intent=original.request_digest, body=body
        )
    result = _summary(data, row, parent)
    checkpoint()
    return _publish(row.guid, result, plan_id, expected_plan_version, plan_digest, review)


def _publish(artifact_id, result, plan_id, expected_plan_version, plan_digest, review):
    caller, original, selected, parent, binding, checkpoint = _source(
        review, plan_id, expected_plan_version, plan_digest
    )
    with transaction.atomic():
        NvkeOperation.objects.select_for_update().get(pk=original.pk)
        NvkeInstallPlan.objects.select_for_update().get(pk=parent.pk)
        checkpoint()
        row = _retained(artifact_id, parent, binding, locked=True)
        _body(row, parent)
        if row.status == "prepared":
            if row.summary != result:
                raise NvkeError("SOURCE_CHANGED", "The immutable chart-artifact summary changed")
        else:
            row.summary, row.status, row.updated_by = result, "prepared", caller
            row.save(update_fields=["summary", "status", "updated_by", "updated_at", "version"])
        checkpoint()
        return _public(row, parent, original, selected)


def read(*, artifact_id, plan_id, expected_plan_version, plan_digest, **review):
    _caller, original, selected, parent, binding, checkpoint = _source(
        review, plan_id, expected_plan_version, plan_digest
    )
    row = _retained(artifact_id, parent, binding)
    _body(row, parent)
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.install_plan_charts(
            str(original.guid),
            intent=original.request_digest,
            plan_id=str(parent.guid),
            plan_digest=plan_digest,
            artifact_id=str(row.guid),
        )
    result = _summary(data, row, parent)
    checkpoint()
    return _publish(row.guid, result, plan_id, expected_plan_version, plan_digest, review)


def recover_preparation(*, artifact_id, plan_id, expected_plan_version, plan_digest, **review):
    _caller, _original, _selected, parent, binding, checkpoint = _source(
        review, plan_id, expected_plan_version, plan_digest
    )
    row = _retained(artifact_id, parent, binding)
    if row.source_binding != binding:
        raise NvkeError(
            "PRECONDITION",
            "Only the original chart preparation caller and token can retry its private intent",
        )
    body = _body(row, parent)
    checkpoint()
    return prepare(
        body=body,
        plan_id=plan_id,
        expected_plan_version=expected_plan_version,
        plan_digest=plan_digest,
        **review,
    )
