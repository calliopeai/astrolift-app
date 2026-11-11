"""Sealed verification intents and historical metadata, never continuation admission."""

import hashlib
import hmac
import json
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from nvke import _operator_install_plan_verification as protocol
from nvke.client import NvkeError
from nvke.operator_contract import ContractError

from astrolift_clusters import nvke_artifact_identity as identities
from astrolift_clusters import nvke_install_plan_contexts as contexts
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters.models import (
    NvkeInstallPlan,
    NvkeInstallPlanCharts,
    NvkeInstallPlanContext,
    NvkeInstallPlanImage,
    NvkeInstallPlanImageBinding,
    NvkeInstallPlanImagePayload,
    NvkeInstallPlanImmutableExecution,
    NvkeInstallPlanTree,
    NvkeInstallPlanVerification,
    NvkeInstallPlanWorkload,
    NvkeOperation,
)
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

CONTEXT_FIELDS = ("context_id", "expected_context_version", "context_digest")


def _source(values):
    caller, original, selected, plan, charts, tree, binding, parent_current = contexts._source(
        {k: v for k, v in values.items() if k not in CONTEXT_FIELDS}
    )
    if (
        type(values["expected_context_version"]) is not int
        or values["expected_context_version"] < 0
        or not isinstance(values["context_digest"], str)
        or not protocol.SHA.fullmatch(values["context_digest"])
    ):
        raise NvkeError("INVALID_INPUT", "Review the exact captured context version and digest")
    context = contexts._retained(values["context_id"], tree, binding)
    if (
        context.version != values["expected_context_version"]
        or context.status != "prepared"
        or context.summary.get("contextDigest") != values["context_digest"]
    ):
        raise NvkeError("SOURCE_CHANGED", "The reviewed render context changed")
    contexts._summary(context.summary, context, plan, charts, tree)
    context_binding = binding
    binding = {
        **binding,
        "context": str(context.guid),
        "context_version": context.version,
        "context_digest": values["context_digest"],
        "context_summary_sha256": hashlib.sha256(outbox.canonical(context.summary)).hexdigest(),
    }

    def current():
        parent_current()
        retained = contexts._retained(context.guid, tree, context_binding)
        if (
            retained.version != context.version
            or retained.status != context.status
            or retained.summary != context.summary
        ):
            raise NvkeError("SOURCE_CHANGED", "The reviewed render context changed")

    current()
    return caller, original, selected, plan, charts, tree, context, binding, current


def _retained(artifact_id, context, binding, *, locked=False):
    query = NvkeInstallPlanVerification.all_objects
    if locked:
        query = query.select_for_update()
    row = query.filter(
        guid=contexts.trees.charts.plans._identity(artifact_id), context=context, deleted_at__isnull=True
    ).first()
    if row is None:
        raise NvkeError("NOT_FOUND", "Render-verification preparation not found")
    if {**row.source_binding, "actor": binding["actor"], "token": binding["token"]} != binding:
        raise NvkeError("SOURCE_CHANGED", "The verification belongs to another reviewed source")
    return row


def _digest(binding, body):
    return hmac.new(
        str(settings.SECRET_KEY).encode(),
        b"nvke.install-plan.verification.prepare.v1\0" + outbox.canonical({"binding": binding, "body": body}),
        hashlib.sha256,
    ).hexdigest()


def _body(row, plan, charts, tree, context):
    try:
        body = protocol.request(
            json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext)))),
            job_id=str(plan.original_operation.guid),
            chart_artifact_id=str(charts.guid),
            read_only=False,
        )
        expected = {
            "artifactId": str(row.guid),
            "planId": str(plan.guid),
            "planDigest": row.source_binding["plan_digest"],
            "treeId": str(tree.guid),
            "treeDigest": row.source_binding["tree_digest"],
            "contextId": str(context.guid),
            "contextDigest": row.source_binding["context_digest"],
            "item": row.source_binding["item"],
        }
        if (
            row.actor_id != row.source_binding["actor"]
            or any(body[k] != v for k, v in expected.items())
            or not hmac.compare_digest(_digest(row.source_binding, body), row.request_digest)
        ):
            raise ValueError
        return body
    except Exception:
        raise NvkeError(
            "PREPARATION_UNAVAILABLE", "The retained private render-verification intent is unavailable"
        ) from None


def _summary(data, row, plan, charts, tree, context):
    try:
        return protocol.summary(
            data,
            body=_body(row, plan, charts, tree, context),
            context=context.summary,
            chart_artifact_id=str(charts.guid),
            chart_artifact_digest=charts.summary["artifactDigest"],
        )
    except Exception:
        raise NvkeError(
            "OPERATOR_INVALID_RESPONSE", "The agent did not confirm the retained render verification"
        ) from None


def _public(row, plan, charts, tree, context, original, selected):
    return {
        "id": str(row.guid),
        "version": row.version,
        "status": row.status,
        "plan_id": str(plan.guid),
        "plan_version": plan.version,
        "chart_artifact_id": str(charts.guid),
        "chart_artifact_version": charts.version,
        "chart_artifact_digest": charts.summary["artifactDigest"],
        "tree_id": str(tree.guid),
        "tree_version": tree.version,
        "context_id": str(context.guid),
        "context_version": context.version,
        "operation_id": str(original.guid),
        "operation_version": original.version,
        "site_id": str(original.site.guid),
        "site_version": original.site.version,
        "authority_id": str(selected.guid),
        "authority_version": selected.version,
        "metadata": row.summary,
    }


def _lock(original, plan, charts, tree, context):
    contexts._lock(original, plan, charts, tree)
    NvkeInstallPlanContext.objects.select_for_update().get(pk=context.pk)


def _publish(artifact_id, result, values):
    caller, original, selected, plan, charts, tree, context, binding, checkpoint = _source(values)
    with transaction.atomic():
        _lock(original, plan, charts, tree, context)
        checkpoint()
        row = _retained(artifact_id, context, binding, locked=True)
        result = _summary(result, row, plan, charts, tree, context)
        if row.status == "prepared":
            if row.summary != result:
                raise NvkeError("SOURCE_CHANGED", "The immutable render-verification summary changed")
        else:
            row.summary, row.status, row.updated_by = result, "prepared", caller
            row.save(update_fields=["summary", "status", "updated_by", "updated_at", "version"])
        checkpoint()
        return _public(row, plan, charts, tree, context, original, selected)


def prepare(*, body, **values):
    caller, original, selected, plan, charts, tree, context, binding, checkpoint = _source(values)
    try:
        body = protocol.request(
            body, job_id=str(original.guid), chart_artifact_id=str(charts.guid), read_only=False
        )
        if any(
            body[k] != v
            for k, v in {
                "planId": str(plan.guid),
                "planDigest": values["plan_digest"],
                "treeId": str(tree.guid),
                "treeDigest": values["tree_digest"],
                "contextId": str(context.guid),
                "contextDigest": values["context_digest"],
                "item": values["item"],
            }.items()
        ):
            raise ValueError
    except (ContractError, ValueError, KeyError, TypeError, OverflowError):
        raise NvkeError(
            "INVALID_INPUT", "Use a bounded verification request for this exact captured context"
        ) from None
    artifact_id, digest = UUID(body["artifactId"]), _digest(binding, body)
    with transaction.atomic():
        _lock(original, plan, charts, tree, context)
        checkpoint()
        identities.lock(artifact_id)
        checkpoint()
        row = NvkeInstallPlanVerification.all_objects.select_for_update().filter(guid=artifact_id).first()
        if row is not None:
            if (
                row.deleted_at is not None
                or row.context_id != context.pk
                or row.actor_id != caller.pk
                or row.source_binding != binding
                or not hmac.compare_digest(row.request_digest, digest)
            ):
                raise NvkeError(
                    "CONFLICT", "This verification identity already identifies another preparation intent"
                )
        else:
            if any(
                model.all_objects.filter(guid=artifact_id).exists()
                for model in (
                    NvkeOperation,
                    NvkeInstallPlan,
                    NvkeInstallPlanCharts,
                    NvkeInstallPlanTree,
                    NvkeInstallPlanContext,
                    NvkeInstallPlanImage,
                    NvkeInstallPlanWorkload,
                    NvkeInstallPlanImageBinding,
                    NvkeInstallPlanImagePayload,
                    NvkeInstallPlanImmutableExecution,
                )
            ):
                raise NvkeError(
                    "CONFLICT", "This identity already identifies another native operation or artifact"
                )
            sealed = encrypt_at_rest(outbox.canonical(body))
            try:
                with transaction.atomic():
                    row = NvkeInstallPlanVerification.objects.create(
                        guid=artifact_id,
                        context=context,
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
                    "CONFLICT", "This verification identity already identifies another preparation intent"
                ) from None
    checkpoint()
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.prepare_install_plan_verification(
            str(original.guid),
            intent=original.request_digest,
            body=body,
            chart_artifact_id=str(charts.guid),
            chart_artifact_digest=values["chart_artifact_digest"],
        )
    result = _summary(data, row, plan, charts, tree, context)
    checkpoint()
    return _publish(row.guid, result, values)


def read(*, artifact_id, **values):
    _caller, original, selected, plan, charts, tree, context, binding, checkpoint = _source(values)
    row = _retained(artifact_id, context, binding)
    body = _body(row, plan, charts, tree, context)
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.install_plan_verification(
            str(original.guid),
            intent=original.request_digest,
            body={k: v for k, v in body.items() if k not in protocol.WRITE_FIELDS},
            chart_artifact_id=str(charts.guid),
            chart_artifact_digest=values["chart_artifact_digest"],
        )
    result = _summary(data, row, plan, charts, tree, context)
    checkpoint()
    return _publish(row.guid, result, values)


def recover_preparation(*, artifact_id, **values):
    _caller, _original, _selected, plan, charts, tree, context, binding, checkpoint = _source(values)
    row = _retained(artifact_id, context, binding)
    if row.source_binding != binding:
        raise NvkeError(
            "PRECONDITION", "Only the original verification caller and token can retry its private intent"
        )
    body = _body(row, plan, charts, tree, context)
    checkpoint()
    return prepare(body=body, **values)
