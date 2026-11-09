"""Durable encrypted tree intents and current-admin inspection/private recovery."""

import hashlib
import hmac
import json
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from nvke import _operator_install_plan_tree as protocol
from nvke.client import NvkeError
from nvke.operator_contract import ContractError

from astrolift_clusters import nvke_artifact_identity as artifact_identity
from astrolift_clusters import nvke_install_plan_charts as charts
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


def _source(
    review,
    plan_id,
    expected_plan_version,
    plan_digest,
    chart_artifact_id,
    expected_chart_artifact_version,
    chart_artifact_digest,
):
    caller, original, selected, parent, plan_binding, plan_current = charts._source(
        review, plan_id, expected_plan_version, plan_digest
    )
    if (
        type(expected_chart_artifact_version) is not int
        or expected_chart_artifact_version < 0
        or not isinstance(chart_artifact_digest, str)
        or not protocol.SHA.fullmatch(chart_artifact_digest)
    ):
        raise NvkeError("INVALID_INPUT", "Review the exact prepared chart-artifact version and digest")
    publication = charts._retained(chart_artifact_id, parent, plan_binding)
    if (
        publication.version != expected_chart_artifact_version
        or publication.status != "prepared"
        or publication.summary.get("artifactDigest") != chart_artifact_digest
    ):
        raise NvkeError("SOURCE_CHANGED", "The reviewed chart publication changed")
    charts._summary(publication.summary, publication, parent)
    binding = {
        **plan_binding,
        "chart_artifact": str(publication.guid),
        "chart_artifact_version": publication.version,
        "chart_artifact_digest": chart_artifact_digest,
        "chart_summary_sha256": hashlib.sha256(outbox.canonical(publication.summary)).hexdigest(),
    }

    def current():
        plan_current()
        value = charts._retained(publication.guid, parent, plan_binding)
        if (
            value.version != publication.version
            or value.status != publication.status
            or value.summary != publication.summary
        ):
            raise NvkeError("SOURCE_CHANGED", "The reviewed chart publication changed")

    current()
    return caller, original, selected, parent, publication, binding, current


def _retained(artifact_id, publication, binding, *, locked=False):
    query = NvkeInstallPlanTree.all_objects
    if locked:
        query = query.select_for_update()
    row = query.filter(
        guid=charts.plans._identity(artifact_id), charts=publication, deleted_at__isnull=True
    ).first()
    if row is None:
        raise NvkeError("NOT_FOUND", "Execution-tree preparation not found")
    observed = {**row.source_binding, "actor": binding["actor"], "token": binding["token"]}
    if observed != binding:
        raise NvkeError("SOURCE_CHANGED", "The execution tree belongs to another reviewed source")
    return row


def _digest(binding, body):
    return hmac.new(
        str(settings.SECRET_KEY).encode(),
        b"nvke.install-plan.tree.prepare.v1\0" + outbox.canonical({"binding": binding, "body": body}),
        hashlib.sha256,
    ).hexdigest()


def _body(row, parent, publication):
    try:
        body = json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))))
        body = protocol.request(body, job_id=str(parent.original_operation.guid), read_only=False)
        if (
            row.actor_id != row.source_binding["actor"]
            or body["artifactId"] != str(row.guid)
            or body["planId"] != str(parent.guid)
            or body["chartArtifactId"] != str(publication.guid)
            or body["planDigest"] != row.source_binding["plan_digest"]
            or body["chartArtifactDigest"] != row.source_binding["chart_artifact_digest"]
            or not hmac.compare_digest(_digest(row.source_binding, body), row.request_digest)
        ):
            raise ValueError
        return body
    except Exception:
        raise NvkeError(
            "PREPARATION_UNAVAILABLE", "The retained private execution-tree intent is unavailable"
        ) from None


def _summary(data, row, parent, publication):
    try:
        body = _body(row, parent, publication)
        return protocol.summary(
            data,
            body=body,
            chart_bytes=publication.summary["bytes"],
            chart_count=publication.summary["chartCount"],
        )
    except Exception:
        raise NvkeError(
            "OPERATOR_INVALID_RESPONSE", "The agent did not confirm the retained execution tree"
        ) from None


def _public(row, parent, publication, original, selected):
    return {
        "id": str(row.guid),
        "version": row.version,
        "status": row.status,
        "plan_id": str(parent.guid),
        "plan_version": parent.version,
        "chart_artifact_id": str(publication.guid),
        "chart_artifact_version": publication.version,
        "operation_id": str(original.guid),
        "operation_version": original.version,
        "site_id": str(original.site.guid),
        "site_version": original.site.version,
        "authority_id": str(selected.guid),
        "authority_version": selected.version,
        "metadata": row.summary,
    }


def prepare(
    *,
    body,
    plan_id,
    expected_plan_version,
    plan_digest,
    chart_artifact_id,
    expected_chart_artifact_version,
    chart_artifact_digest,
    **review,
):
    source = (
        plan_id,
        expected_plan_version,
        plan_digest,
        chart_artifact_id,
        expected_chart_artifact_version,
        chart_artifact_digest,
    )
    caller, original, selected, parent, publication, binding, checkpoint = _source(review, *source)
    try:
        body = protocol.request(body, job_id=str(original.guid), read_only=False)
        if (
            body["planId"] != str(parent.guid)
            or body["planDigest"] != plan_digest
            or body["chartArtifactId"] != str(publication.guid)
            or body["chartArtifactDigest"] != chart_artifact_digest
        ):
            raise ValueError
    except (ContractError, ValueError, KeyError, TypeError, OverflowError):
        raise NvkeError(
            "INVALID_INPUT", "Use a bounded tree request for these exact plan and chart publications"
        ) from None
    artifact_id = UUID(body["artifactId"])
    digest = _digest(binding, body)
    with transaction.atomic():
        NvkeOperation.objects.select_for_update().get(pk=original.pk)
        NvkeInstallPlan.objects.select_for_update().get(pk=parent.pk)
        NvkeInstallPlanCharts.objects.select_for_update().get(pk=publication.pk)
        checkpoint()
        artifact_identity.lock(artifact_id)
        checkpoint()
        row = NvkeInstallPlanTree.all_objects.select_for_update().filter(guid=artifact_id).first()
        if row is not None:
            if (
                row.deleted_at is not None
                or row.charts_id != publication.pk
                or row.actor_id != caller.pk
                or row.source_binding != binding
                or not hmac.compare_digest(row.request_digest, digest)
            ):
                raise NvkeError(
                    "CONFLICT", "This tree identity already identifies another preparation intent"
                )
        else:
            if any(
                model.all_objects.filter(guid=artifact_id).exists()
                for model in (NvkeOperation, NvkeInstallPlan, NvkeInstallPlanCharts, NvkeInstallPlanContext)
            ):
                raise NvkeError(
                    "CONFLICT", "This identity already identifies an operation, plan or chart publication"
                )
            sealed = encrypt_at_rest(outbox.canonical(body))
            try:
                with transaction.atomic():
                    row = NvkeInstallPlanTree.objects.create(
                        guid=artifact_id,
                        charts=publication,
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
                    "CONFLICT", "This tree identity already identifies another preparation intent"
                ) from None
    checkpoint()
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.prepare_install_plan_tree(str(original.guid), intent=original.request_digest, body=body)
    result = _summary(data, row, parent, publication)
    checkpoint()
    return _publish(row.guid, result, source, review)


def _publish(artifact_id, result, source, review):
    caller, original, selected, parent, publication, binding, checkpoint = _source(review, *source)
    with transaction.atomic():
        NvkeOperation.objects.select_for_update().get(pk=original.pk)
        NvkeInstallPlan.objects.select_for_update().get(pk=parent.pk)
        NvkeInstallPlanCharts.objects.select_for_update().get(pk=publication.pk)
        checkpoint()
        row = _retained(artifact_id, publication, binding, locked=True)
        _body(row, parent, publication)
        if row.status == "prepared":
            if row.summary != result:
                raise NvkeError("SOURCE_CHANGED", "The immutable execution-tree summary changed")
        else:
            row.summary, row.status, row.updated_by = result, "prepared", caller
            row.save(update_fields=["summary", "status", "updated_by", "updated_at", "version"])
        checkpoint()
        return _public(row, parent, publication, original, selected)


def read(
    *,
    artifact_id,
    plan_id,
    expected_plan_version,
    plan_digest,
    chart_artifact_id,
    expected_chart_artifact_version,
    chart_artifact_digest,
    **review,
):
    source = (
        plan_id,
        expected_plan_version,
        plan_digest,
        chart_artifact_id,
        expected_chart_artifact_version,
        chart_artifact_digest,
    )
    _caller, original, selected, parent, publication, binding, checkpoint = _source(review, *source)
    row = _retained(artifact_id, publication, binding)
    _body(row, parent, publication)
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.install_plan_tree(
            str(original.guid),
            intent=original.request_digest,
            plan_id=str(parent.guid),
            plan_digest=plan_digest,
            chart_artifact_id=str(publication.guid),
            chart_artifact_digest=chart_artifact_digest,
            artifact_id=str(row.guid),
        )
    result = _summary(data, row, parent, publication)
    checkpoint()
    return _publish(row.guid, result, source, review)


def recover_preparation(
    *,
    artifact_id,
    plan_id,
    expected_plan_version,
    plan_digest,
    chart_artifact_id,
    expected_chart_artifact_version,
    chart_artifact_digest,
    **review,
):
    source = (
        plan_id,
        expected_plan_version,
        plan_digest,
        chart_artifact_id,
        expected_chart_artifact_version,
        chart_artifact_digest,
    )
    _caller, _original, _selected, parent, publication, binding, checkpoint = _source(review, *source)
    row = _retained(artifact_id, publication, binding)
    if row.source_binding != binding:
        raise NvkeError(
            "PRECONDITION", "Only the original tree preparation caller and token can retry its private intent"
        )
    body = _body(row, parent, publication)
    checkpoint()
    return prepare(
        body=body,
        plan_id=plan_id,
        expected_plan_version=expected_plan_version,
        plan_digest=plan_digest,
        chart_artifact_id=chart_artifact_id,
        expected_chart_artifact_version=expected_chart_artifact_version,
        chart_artifact_digest=chart_artifact_digest,
        **review,
    )
