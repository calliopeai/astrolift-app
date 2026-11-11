"""Encrypted pull-secret analysis intents and explicit original-source-bound recovery."""

import hashlib
import hmac
import json
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from nvke import _operator_install_plan_pull_secret_analysis as protocol
from nvke.client import NvkeError
from nvke.operator_contract import ContractError

from astrolift_clusters import nvke_artifact_identity as identities
from astrolift_clusters import nvke_install_plan_image_bindings as bindings
from astrolift_clusters import nvke_install_plan_immutable as immutable
from astrolift_clusters import nvke_install_plan_pull_authority as parents
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters.models import NvkeInstallPlanPullAuthority, NvkeInstallPlanPullSecretAnalysis
from astrolift_clusters.nvke_install_plan_immutable import _sdk_source
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

SOURCE_FIELDS = ("pull_authority_id", "expected_pull_authority_version", "pull_authority_digest")


def _source(values):
    caller, original, selected, plan, charts, tree, binding, base, parent_check = immutable._source(
        {key: value for key, value in values.items() if key not in SOURCE_FIELDS}
    )
    if (
        type(values["expected_pull_authority_version"]) is not int
        or values["expected_pull_authority_version"] < 1
        or not isinstance(values["pull_authority_digest"], str)
        or not bindings.protocol.SHA.fullmatch(values["pull_authority_digest"])
    ):
        raise NvkeError("INVALID_INPUT", "Review the exact pull observation version and digest")

    def current():
        row = parents._retained(values["pull_authority_id"], binding, base)
        if (
            row.status != "prepared"
            or row.version != values["expected_pull_authority_version"]
            or row.summary.get("authorityDigest") != values["pull_authority_digest"]
        ):
            raise NvkeError("SOURCE_CHANGED", "The reviewed pull observation changed")
        parents._summary(row.summary, row, plan, charts, tree, binding)
        return row

    parent = current()
    source = {
        **base,
        "pull_authority": str(parent.guid),
        "pull_authority_version": parent.version,
        "pull_authority_digest": values["pull_authority_digest"],
        "pull_authority_summary_sha256": hashlib.sha256(outbox.canonical(parent.summary)).hexdigest(),
    }

    def checkpoint():
        parent_check()
        row = current()
        if row.summary != parent.summary:
            raise NvkeError("SOURCE_CHANGED", "The reviewed pull observation summary changed")

    checkpoint()
    return caller, original, selected, plan, charts, tree, binding, source, checkpoint


def _lock(original, plan, charts, execution_tree, binding, source):
    immutable._lock(original, plan, charts, execution_tree, binding)
    NvkeInstallPlanPullAuthority.all_objects.select_for_update().get(guid=source["pull_authority"])


def _public(row, plan, charts, execution_tree, binding, original, selected):
    return {
        **immutable._public(row, plan, charts, execution_tree, binding, original, selected),
        "pull_authority_id": row.source_binding["pull_authority"],
        "pull_authority_version": row.source_binding["pull_authority_version"],
        "pull_authority_digest": row.source_binding["pull_authority_digest"],
    }


def _digest(source, body):
    domain = b"nvke.install-plan.pull-secret-analysis.prepare.v1\x00"
    return hmac.new(
        str(settings.SECRET_KEY).encode(),
        domain + outbox.canonical({"binding": source, "body": body}),
        hashlib.sha256,
    ).hexdigest()


def _retained(artifact_id, binding, source, *, locked=False):
    query = NvkeInstallPlanPullSecretAnalysis.all_objects
    if locked:
        query = query.select_for_update()
    row = query.filter(
        guid=bindings.trees.charts.plans._identity(artifact_id), binding=binding, deleted_at__isnull=True
    ).first()
    if row is None:
        raise NvkeError("NOT_FOUND", "Secret candidate analysis not found")
    if {**row.source_binding, "actor": source["actor"], "token": source["token"]} != source:
        raise NvkeError(
            "SOURCE_CHANGED", "The pull-secret analysis observation belongs to another reviewed source"
        )
    return row


def _body(row, plan, charts, execution_tree, binding):
    try:
        source = row.source_binding
        body = protocol.request(
            json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext)))),
            job_id=str(plan.original_operation.guid),
            chart_artifact_id=str(charts.guid),
            workload_id=source["workload"],
            read_only=False,
        )
        if (
            row.actor_id != source["actor"]
            or str(row.pull_authority.guid) != source["pull_authority"]
            or any(
                (
                    body[k] != v
                    for k, v in {
                        "artifactId": str(row.guid),
                        "planId": str(plan.guid),
                        "planDigest": source["plan_digest"],
                        "treeId": str(execution_tree.guid),
                        "treeDigest": source["tree_digest"],
                        "bindingId": str(binding.guid),
                        "bindingDigest": source["image_binding_digest"],
                        "pullAuthorityId": source["pull_authority"],
                        "pullAuthorityDigest": source["pull_authority_digest"],
                    }.items()
                )
            )
            or (not hmac.compare_digest(_digest(source, body), row.request_digest))
        ):
            raise ValueError
        return body
    except Exception:
        raise NvkeError(
            "PREPARATION_UNAVAILABLE", "The retained private pull-secret analysis intent is unavailable"
        ) from None


def _publish(artifact_id, data, values):
    caller, original, selected, plan, charts, execution_tree, binding, source, checkpoint = _source(values)
    with transaction.atomic():
        _lock(original, plan, charts, execution_tree, binding, source)
        checkpoint()
        row = _retained(artifact_id, binding, source, locked=True)
        result = _summary(data, row, plan, charts, execution_tree, binding)
        if row.status == "prepared":
            if row.summary != result:
                raise NvkeError("SOURCE_CHANGED", "The pull-secret analysis observation summary changed")
        else:
            row.summary, row.status, row.updated_by = (result, "prepared", caller)
            row.save(update_fields=["summary", "status", "updated_by", "updated_at", "version"])
        checkpoint()
        return _public(row, plan, charts, execution_tree, binding, original, selected)


def prepare(*, body, **values):
    caller, original, selected, plan, charts, execution_tree, binding, source, checkpoint = _source(values)
    try:
        body = protocol.request(
            body,
            job_id=str(original.guid),
            chart_artifact_id=str(charts.guid),
            workload_id=source["workload"],
            read_only=False,
        )
        if any(
            (
                body[k] != v
                for k, v in {
                    "planId": str(plan.guid),
                    "planDigest": source["plan_digest"],
                    "treeId": str(execution_tree.guid),
                    "treeDigest": source["tree_digest"],
                    "bindingId": str(binding.guid),
                    "bindingDigest": source["image_binding_digest"],
                    "pullAuthorityId": source["pull_authority"],
                    "pullAuthorityDigest": source["pull_authority_digest"],
                }.items()
            )
        ):
            raise ValueError
    except (ContractError, ValueError, KeyError, TypeError, OverflowError):
        raise NvkeError(
            "INVALID_INPUT", "Use the exact reviewed image binding and bounded pull-secret analysis request"
        ) from None
    checkpoint()
    artifact_id, digest = (UUID(body["artifactId"]), _digest(source, body))
    model = NvkeInstallPlanPullSecretAnalysis
    with transaction.atomic():
        _lock(original, plan, charts, execution_tree, binding, source)
        checkpoint()
        identities.lock(artifact_id)
        checkpoint()
        row = model.all_objects.select_for_update().filter(guid=artifact_id).first()
        if row is not None:
            if (
                row.deleted_at is not None
                or row.binding_id != binding.pk
                or row.actor_id != caller.pk
                or (row.source_binding != source)
                or (not hmac.compare_digest(row.request_digest, digest))
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
                        pull_authority=NvkeInstallPlanPullAuthority.all_objects.get(
                            guid=source["pull_authority"]
                        ),
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
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.prepare_install_plan_pull_secret_analysis(
            str(original.guid), body=body, **_sdk_source(original, charts, source)
        )
    result = _summary(data, row, plan, charts, execution_tree, binding)
    checkpoint()
    return _publish(row.guid, result, values)


def read(*, artifact_id, **values):
    _caller, original, selected, plan, charts, execution_tree, binding, source, checkpoint = _source(values)
    row = _retained(artifact_id, binding, source)
    body = _body(row, plan, charts, execution_tree, binding)
    with authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.install_plan_pull_secret_analysis(
            str(original.guid),
            body={k: v for k, v in body.items() if k in protocol.BASE},
            **_sdk_source(original, charts, source),
        )
    result = _summary(data, row, plan, charts, execution_tree, binding)
    checkpoint()
    return _publish(row.guid, result, values)


def recover_preparation(*, artifact_id, **values):
    _caller, _original, _selected, plan, charts, execution_tree, binding, source, checkpoint = _source(values)
    row = _retained(artifact_id, binding, source)
    if row.source_binding != source:
        raise NvkeError("PRECONDITION", "Only the original caller and token can retry this private intent")
    body = _body(row, plan, charts, execution_tree, binding)
    checkpoint()
    return prepare(body=body, **values)


def _summary(data, row, plan, charts, execution_tree, binding):
    try:
        body = _body(row, plan, charts, execution_tree, binding)
        parent = NvkeInstallPlanPullAuthority.all_objects.get(guid=row.source_binding["pull_authority"])
        return protocol.summary(
            data, body=body, source={"binding": binding.summary, "pullAuthority": parent.summary}
        )
    except Exception:
        raise NvkeError(
            "OPERATOR_INVALID_RESPONSE",
            "The agent did not confirm the retained pull-secret analysis observation",
        ) from None
