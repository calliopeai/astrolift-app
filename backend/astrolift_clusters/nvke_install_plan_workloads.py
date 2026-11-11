"""Encrypted workload-inventory intents, source-bound reads and explicit recovery."""

import hashlib
import hmac
import json
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from nvke import _operator_install_plan_workload as protocol
from nvke.client import NvkeError
from nvke.operator_contract import ContractError

from astrolift_clusters import nvke_artifact_identity as artifact_identity
from astrolift_clusters import nvke_install_plan_contexts as contexts
from astrolift_clusters import nvke_install_plan_images as images
from astrolift_clusters import nvke_install_plan_trees as trees
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters.models import (
    NvkeInstallPlan,
    NvkeInstallPlanCharts,
    NvkeInstallPlanContext,
    NvkeInstallPlanTree,
    NvkeInstallPlanWorkload,
    NvkeOperation,
)
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest


def _source(values):
    return images._source(values)


def _contexts(body, parent, publication, tree):
    bindings = body["contextBindings"]
    rows = list(
        NvkeInstallPlanContext.all_objects.filter(
            guid__in=[value["contextId"] for value in bindings.values()],
            tree=tree,
            deleted_at__isnull=True,
            status="prepared",
        )
    )
    if len(rows) != len(bindings):
        raise NvkeError("SOURCE_CHANGED", "The reviewed render contexts are unavailable")
    by_id = {str(row.guid): row for row in rows}
    for name, binding in bindings.items():
        row = by_id[binding["contextId"]]
        result = contexts._summary(row.summary, row, parent, publication, tree)
        if result["item"] != name or result["contextDigest"] != binding["contextDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The reviewed render context changed")


def _retained(artifact_id, tree, binding, *, locked=False):
    query = NvkeInstallPlanWorkload.all_objects
    if locked:
        query = query.select_for_update()
    row = query.filter(
        guid=trees.charts.plans._identity(artifact_id), tree=tree, deleted_at__isnull=True
    ).first()
    if row is None:
        raise NvkeError("NOT_FOUND", "Workload inventory not found")
    if {**row.source_binding, "actor": binding["actor"], "token": binding["token"]} != binding:
        raise NvkeError("SOURCE_CHANGED", "The workload inventory belongs to another reviewed source")
    return row


def _digest(binding, body):
    return hmac.new(
        str(settings.SECRET_KEY).encode(),
        b"nvke.install-plan.workload.prepare.v1\0" + outbox.canonical({"binding": binding, "body": body}),
        hashlib.sha256,
    ).hexdigest()


def _body(row, parent, publication, tree):
    try:
        body = protocol.request(
            json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext)))),
            job_id=str(parent.original_operation.guid),
            read_only=False,
            chart_artifact_id=str(publication.guid),
        )
        if (
            row.actor_id != row.source_binding["actor"]
            or body["artifactId"] != str(row.guid)
            or body["planId"] != str(parent.guid)
            or body["planDigest"] != row.source_binding["plan_digest"]
            or body["treeId"] != str(tree.guid)
            or body["treeDigest"] != row.source_binding["tree_digest"]
            or not hmac.compare_digest(_digest(row.source_binding, body), row.request_digest)
        ):
            raise ValueError
        _contexts(body, parent, publication, tree)
        return body
    except Exception:
        raise NvkeError(
            "PREPARATION_UNAVAILABLE", "The retained private workload intent is unavailable"
        ) from None


def _summary(data, row, parent, publication, tree):
    try:
        return protocol.summary(
            data, body=_body(row, parent, publication, tree), item_count=parent.summary["itemCount"]
        )
    except Exception:
        raise NvkeError(
            "OPERATOR_INVALID_RESPONSE", "The agent did not confirm the retained workload inventory"
        ) from None


def _public(row, parent, publication, tree, original, selected):
    return {
        "id": str(row.guid),
        "version": row.version,
        "status": row.status,
        "plan_id": str(parent.guid),
        "plan_version": parent.version,
        "plan_digest": row.source_binding["plan_digest"],
        "chart_artifact_id": str(publication.guid),
        "chart_artifact_version": publication.version,
        "chart_artifact_digest": publication.summary["artifactDigest"],
        "tree_id": str(tree.guid),
        "tree_version": tree.version,
        "tree_digest": row.source_binding["tree_digest"],
        "operation_id": str(original.guid),
        "operation_version": original.version,
        "site_id": str(original.site.guid),
        "site_version": original.site.version,
        "authority_id": str(selected.guid),
        "authority_version": selected.version,
        "metadata": row.summary,
    }


def _lock(original, parent, publication, tree):
    NvkeOperation.objects.select_for_update().get(pk=original.pk)
    NvkeInstallPlan.objects.select_for_update().get(pk=parent.pk)
    NvkeInstallPlanCharts.objects.select_for_update().get(pk=publication.pk)
    NvkeInstallPlanTree.objects.select_for_update().get(pk=tree.pk)


def _publish(artifact_id, result, values):
    caller, original, selected, parent, publication, tree, binding, checkpoint = _source(values)
    with transaction.atomic():
        _lock(original, parent, publication, tree)
        checkpoint()
        row = _retained(artifact_id, tree, binding, locked=True)
        result = _summary(result, row, parent, publication, tree)
        if row.status == "prepared":
            if row.summary != result:
                raise NvkeError("SOURCE_CHANGED", "The immutable workload summary changed")
        else:
            row.summary, row.status, row.updated_by = result, "prepared", caller
            row.save(update_fields=["summary", "status", "updated_by", "updated_at", "version"])
        checkpoint()
        return _public(row, parent, publication, tree, original, selected)


def prepare(*, body, **values):
    caller, original, selected, parent, publication, tree, binding, checkpoint = _source(values)
    try:
        body = protocol.request(
            body, job_id=str(original.guid), read_only=False, chart_artifact_id=str(publication.guid)
        )
        if any(
            body[k] != v
            for k, v in {
                "planId": str(parent.guid),
                "planDigest": values["plan_digest"],
                "treeId": str(tree.guid),
                "treeDigest": values["tree_digest"],
            }.items()
        ):
            raise ValueError
    except (ContractError, ValueError, KeyError, TypeError, OverflowError):
        raise NvkeError(
            "INVALID_INPUT", "Use exact render-context bindings and a bounded workload request for this tree"
        ) from None
    _contexts(body, parent, publication, tree)
    artifact_id, digest = UUID(body["artifactId"]), _digest(binding, body)
    with transaction.atomic():
        _lock(original, parent, publication, tree)
        checkpoint()
        artifact_identity.lock(artifact_id)
        checkpoint()
        row = NvkeInstallPlanWorkload.all_objects.select_for_update().filter(guid=artifact_id).first()
        if row is not None:
            if (
                row.deleted_at is not None
                or row.tree_id != tree.pk
                or row.actor_id != caller.pk
                or row.source_binding != binding
                or not hmac.compare_digest(row.request_digest, digest)
            ):
                raise NvkeError(
                    "CONFLICT", "This workload identity already identifies another preparation intent"
                )
        else:
            if artifact_identity.reserved(artifact_id, excluding=NvkeInstallPlanWorkload):
                raise NvkeError(
                    "CONFLICT", "This identity already identifies another native operation or artifact"
                )
            sealed = encrypt_at_rest(outbox.canonical(body))
            try:
                with transaction.atomic():
                    row = NvkeInstallPlanWorkload.objects.create(
                        guid=artifact_id,
                        tree=tree,
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
                    "CONFLICT", "This workload identity already identifies another preparation intent"
                ) from None
    checkpoint()
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.prepare_install_plan_workload(
            str(original.guid),
            intent=original.request_digest,
            body=body,
            chart_artifact_id=str(publication.guid),
            chart_artifact_digest=values["chart_artifact_digest"],
        )
    result = _summary(data, row, parent, publication, tree)
    checkpoint()
    return _publish(row.guid, result, values)


def read(*, artifact_id, **values):
    _caller, original, selected, parent, publication, tree, binding, checkpoint = _source(values)
    row = _retained(artifact_id, tree, binding)
    body = _body(row, parent, publication, tree)
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.install_plan_workload(
            str(original.guid),
            intent=original.request_digest,
            body={k: v for k, v in body.items() if k in protocol.BASE},
            chart_artifact_id=str(publication.guid),
            chart_artifact_digest=values["chart_artifact_digest"],
        )
    result = _summary(data, row, parent, publication, tree)
    checkpoint()
    return _publish(row.guid, result, values)


def recover_preparation(*, artifact_id, **values):
    _caller, _original, _selected, parent, publication, tree, binding, checkpoint = _source(values)
    row = _retained(artifact_id, tree, binding)
    if row.source_binding != binding:
        raise NvkeError(
            "PRECONDITION", "Only the original workload caller and token can retry its private intent"
        )
    body = _body(row, parent, publication, tree)
    checkpoint()
    return prepare(body=body, **values)
