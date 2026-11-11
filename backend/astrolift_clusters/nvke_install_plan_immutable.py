"""Encrypted immutable-artifact intents and explicit source-bound recovery."""

import hashlib
import hmac
import json
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from nvke import _operator_install_plan_immutable as protocol
from nvke.client import NvkeError
from nvke.operator_contract import ContractError

from astrolift_clusters import nvke_artifact_identity as identities
from astrolift_clusters import nvke_install_plan_image_bindings as bindings
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters.models import (
    NvkeInstallPlanImageBinding,
    NvkeInstallPlanImagePayload,
    NvkeInstallPlanImmutableExecution,
)
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

SOURCE_FIELDS = ("binding_id", "expected_binding_version", "binding_digest")


def _model(tree):
    return NvkeInstallPlanImmutableExecution if tree else NvkeInstallPlanImagePayload


def _source(values):
    parent_values = {k: v for k, v in values.items() if k not in SOURCE_FIELDS}
    caller, original, selected, plan, charts, tree, base, parent_check = bindings._source(parent_values)
    if (
        type(values["expected_binding_version"]) is not int
        or values["expected_binding_version"] < 1
        or not isinstance(values["binding_digest"], str)
        or not bindings.protocol.SHA.fullmatch(values["binding_digest"])
    ):
        raise NvkeError("INVALID_INPUT", "Review the exact prepared image binding version and digest")

    def current_binding():
        row = bindings._retained(values["binding_id"], tree, base)
        if (
            row.status != "prepared"
            or row.version != values["expected_binding_version"]
            or row.summary.get("bindingDigest") != values["binding_digest"]
        ):
            raise NvkeError("SOURCE_CHANGED", "The reviewed image binding changed")
        bindings._summary(row.summary, row, plan, charts, tree)
        return row

    image_binding = current_binding()
    source = {
        **base,
        "image_binding": str(image_binding.guid),
        "image_binding_version": image_binding.version,
        "image_binding_digest": values["binding_digest"],
        "image_binding_summary_sha256": hashlib.sha256(outbox.canonical(image_binding.summary)).hexdigest(),
    }

    def checkpoint():
        parent_check()
        row = current_binding()
        if row.summary != image_binding.summary:
            raise NvkeError("SOURCE_CHANGED", "The reviewed image binding summary changed")

    checkpoint()
    return caller, original, selected, plan, charts, tree, image_binding, source, checkpoint


def _digest(source, body, *, tree):
    domain = (
        b"nvke.install-plan.immutable-execution.prepare.v1\0"
        if tree
        else b"nvke.install-plan.image-payload.prepare.v1\0"
    )
    return hmac.new(
        str(settings.SECRET_KEY).encode(),
        domain + outbox.canonical({"binding": source, "body": body}),
        hashlib.sha256,
    ).hexdigest()


def _retained(artifact_id, binding, source, *, tree, locked=False):
    query = _model(tree).all_objects
    if locked:
        query = query.select_for_update()
    row = query.filter(
        guid=bindings.trees.charts.plans._identity(artifact_id), binding=binding, deleted_at__isnull=True
    ).first()
    if row is None:
        raise NvkeError("NOT_FOUND", "Immutable install artifact not found")
    if {**row.source_binding, "actor": source["actor"], "token": source["token"]} != source:
        raise NvkeError("SOURCE_CHANGED", "The immutable artifact belongs to another reviewed source")
    return row


def _payloads(body, plan, charts, execution_tree, binding, source):
    selected = body["payloadBindings"]
    if len(selected) != plan.summary["itemCount"]:
        raise NvkeError("SOURCE_CHANGED", "Select an immutable payload for every original phase")
    for name, choice in selected.items():
        row = _retained(choice["artifactId"], binding, source, tree=False)
        if row.status != "prepared":
            raise NvkeError("SOURCE_CHANGED", "A selected phase payload is not prepared")
        intent = _body(row, plan, charts, execution_tree, binding, tree=False)
        observed = _summary(row.summary, row, plan, charts, execution_tree, binding, tree=False)
        if intent["item"] != name or observed["artifactDigest"] != choice["artifactDigest"]:
            raise NvkeError("SOURCE_CHANGED", "A selected phase payload changed")


def _body(row, plan, charts, execution_tree, binding, *, tree):
    try:
        source = row.source_binding
        body = protocol.request(
            json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext)))),
            job_id=str(plan.original_operation.guid),
            chart_artifact_id=str(charts.guid),
            workload_id=source["workload"],
            read_only=False,
            tree=tree,
        )
        if (
            row.actor_id != source["actor"]
            or any(
                body[k] != v
                for k, v in {
                    "artifactId": str(row.guid),
                    "planId": str(plan.guid),
                    "planDigest": source["plan_digest"],
                    "treeId": str(execution_tree.guid),
                    "treeDigest": source["tree_digest"],
                    "bindingId": str(binding.guid),
                    "bindingDigest": source["image_binding_digest"],
                }.items()
            )
            or not hmac.compare_digest(_digest(source, body, tree=tree), row.request_digest)
        ):
            raise ValueError
        if tree:
            _payloads(body, plan, charts, execution_tree, binding, source)
        return body
    except Exception:
        raise NvkeError(
            "PREPARATION_UNAVAILABLE", "The retained private immutable-artifact intent is unavailable"
        ) from None


def _summary(data, row, plan, charts, execution_tree, binding, *, tree):
    try:
        body = _body(row, plan, charts, execution_tree, binding, tree=tree)
        workload = bindings._workload(
            {
                "workloadId": row.source_binding["workload"],
                "workloadDigest": row.source_binding["workload_digest"],
            },
            plan,
            charts,
            execution_tree,
        )
        return protocol.summary(
            data,
            body=body,
            source={"binding": binding.summary, "metadata": plan.summary, "workload": workload},
            tree=tree,
        )
    except Exception:
        raise NvkeError(
            "OPERATOR_INVALID_RESPONSE", "The agent did not confirm the retained immutable artifact"
        ) from None


def _public(row, plan, charts, execution_tree, binding, original, selected):
    return {
        **bindings._public(binding, plan, charts, execution_tree, original, selected),
        "id": str(row.guid),
        "version": row.version,
        "status": row.status,
        "binding_id": str(binding.guid),
        "binding_version": binding.version,
        "binding_digest": row.source_binding["image_binding_digest"],
        "metadata": row.summary,
    }


def _lock(original, plan, charts, execution_tree, binding):
    bindings._lock(original, plan, charts, execution_tree)
    NvkeInstallPlanImageBinding.objects.select_for_update().get(pk=binding.pk)


def _publish(artifact_id, data, values, *, tree):
    caller, original, selected, plan, charts, execution_tree, binding, source, checkpoint = _source(values)
    with transaction.atomic():
        _lock(original, plan, charts, execution_tree, binding)
        checkpoint()
        row = _retained(artifact_id, binding, source, tree=tree, locked=True)
        result = _summary(data, row, plan, charts, execution_tree, binding, tree=tree)
        if row.status == "prepared":
            if row.summary != result:
                raise NvkeError("SOURCE_CHANGED", "The immutable artifact summary changed")
        else:
            row.summary, row.status, row.updated_by = result, "prepared", caller
            row.save(update_fields=["summary", "status", "updated_by", "updated_at", "version"])
        checkpoint()
        return _public(row, plan, charts, execution_tree, binding, original, selected)


def _sdk_source(original, charts, source):
    return {
        "intent": original.request_digest,
        "chart_artifact_id": str(charts.guid),
        "chart_artifact_digest": charts.summary["artifactDigest"],
        "workload_id": source["workload"],
        "workload_digest": source["workload_digest"],
    }


def prepare(*, body, tree=False, **values):
    caller, original, selected, plan, charts, execution_tree, binding, source, checkpoint = _source(values)
    try:
        body = protocol.request(
            body,
            job_id=str(original.guid),
            chart_artifact_id=str(charts.guid),
            workload_id=source["workload"],
            read_only=False,
            tree=tree,
        )
        if any(
            body[k] != v
            for k, v in {
                "planId": str(plan.guid),
                "planDigest": source["plan_digest"],
                "treeId": str(execution_tree.guid),
                "treeDigest": source["tree_digest"],
                "bindingId": str(binding.guid),
                "bindingDigest": source["image_binding_digest"],
            }.items()
        ):
            raise ValueError
    except (ContractError, ValueError, KeyError, TypeError, OverflowError):
        raise NvkeError(
            "INVALID_INPUT", "Use the exact reviewed image binding and bounded immutable-artifact request"
        ) from None

    parent_check = checkpoint

    def checkpoint():
        parent_check()
        if tree:
            _payloads(body, plan, charts, execution_tree, binding, source)

    checkpoint()
    artifact_id, digest = UUID(body["artifactId"]), _digest(source, body, tree=tree)
    model = _model(tree)
    with transaction.atomic():
        _lock(original, plan, charts, execution_tree, binding)
        checkpoint()
        identities.lock(artifact_id)
        checkpoint()
        row = model.all_objects.select_for_update().filter(guid=artifact_id).first()
        if row is not None:
            if (
                row.deleted_at is not None
                or row.binding_id != binding.pk
                or row.actor_id != caller.pk
                or row.source_binding != source
                or not hmac.compare_digest(row.request_digest, digest)
            ):
                raise NvkeError("CONFLICT", "This identity already identifies another preparation intent")
        else:
            if identities.reserved(artifact_id, excluding=model):
                raise NvkeError("CONFLICT", "This identity already identifies a native operation or artifact")
            sealed = encrypt_at_rest(outbox.canonical(body))
            try:
                with transaction.atomic():
                    row = model.objects.create(
                        guid=artifact_id,
                        binding=binding,
                        actor=caller,
                        source_binding=source,
                        request_digest=digest,
                        input_backend_kind=sealed.backend_kind,
                        input_ciphertext=sealed.backend_ref,
                        created_by=caller,
                        updated_by=caller,
                    )
            except IntegrityError:
                raise NvkeError(
                    "CONFLICT", "This identity already identifies another preparation intent"
                ) from None
    checkpoint()
    method = "prepare_install_plan_immutable_execution_tree" if tree else "prepare_install_plan_image_payload"
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = getattr(client, method)(str(original.guid), body=body, **_sdk_source(original, charts, source))
    result = _summary(data, row, plan, charts, execution_tree, binding, tree=tree)
    checkpoint()
    return _publish(row.guid, result, values, tree=tree)


def read(*, artifact_id, tree=False, **values):
    _caller, original, selected, plan, charts, execution_tree, binding, source, checkpoint = _source(values)
    row = _retained(artifact_id, binding, source, tree=tree)
    body = _body(row, plan, charts, execution_tree, binding, tree=tree)
    parent_check = checkpoint

    def checkpoint():
        parent_check()
        if tree:
            _payloads(body, plan, charts, execution_tree, binding, source)

    method = "install_plan_immutable_execution_tree" if tree else "install_plan_image_payload"
    options = {"payload_bindings": body["payloadBindings"]} if tree else {}
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = getattr(client, method)(
            str(original.guid),
            body={k: v for k, v in body.items() if k in protocol.BASE or (not tree and k == "item")},
            **options,
            **_sdk_source(original, charts, source),
        )
    result = _summary(data, row, plan, charts, execution_tree, binding, tree=tree)
    checkpoint()
    return _publish(row.guid, result, values, tree=tree)


def recover_preparation(*, artifact_id, tree=False, **values):
    _caller, _original, _selected, plan, charts, execution_tree, binding, source, checkpoint = _source(values)
    row = _retained(artifact_id, binding, source, tree=tree)
    if row.source_binding != source:
        raise NvkeError("PRECONDITION", "Only the original caller and token can retry this private intent")
    body = _body(row, plan, charts, execution_tree, binding, tree=tree)
    checkpoint()
    return prepare(body=body, tree=tree, **values)
