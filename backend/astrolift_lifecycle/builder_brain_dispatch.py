"""Encrypted exact normal-deployment intent; once-only engine submission."""

import hashlib
import json
from dataclasses import asdict

from django.db import transaction
from django.utils import timezone

from astrolift_lifecycle import builder_brain_source as source
from astrolift_lifecycle.models import BuilderBrainBuild
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest


class Capture:
    """Called inside ordinary deployment admission, after native origin capture."""

    def __init__(self, row):
        self.row = row

    def capture(self, **admitted):
        row = self.row
        deployment = admitted["deployment"]
        if (
            admitted["workflow_kind"] != "DeployAppWorkflow"
            or deployment.registered_app.project_id != row.project_id
            or deployment.registered_app.organization_id != row.organization_id
            or deployment.triggered_by_user_id != row.actor_id
            or len(admitted["args"]) != 1
            or row.dispatch_ciphertext
        ):
            raise source.SourceError(
                "DEPLOY_INTENT_CONFLICT",
                "the normal deployment intent differs from this source hosting request",
            )
        payload = {
            "workflow_kind": admitted["workflow_kind"],
            "workflow_id": admitted["workflow_id"],
            "args": [asdict(value) for value in admitted["args"]],
            "organization_id": admitted["organization_id"],
            "registered_app_id": admitted["registered_app_id"],
            "app_environment_id": admitted["app_environment_id"],
            "actor": asdict(admitted["actor"]),
            "run_trigger": admitted["run_trigger"],
            "manifest_sha256": hashlib.sha256(deployment.registered_app.manifest_raw.encode()).hexdigest(),
            "cluster_guid": str(deployment.app_environment.tenant_cluster.guid),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        encrypted = encrypt_at_rest(raw)
        row.dispatch_backend_kind = encrypted.backend_kind
        row.dispatch_ciphertext = encrypted.backend_ref
        row.dispatch_sha256 = hashlib.sha256(raw).hexdigest()
        row.deployment_workflow_id = admitted["workflow_id"]
        row.deployment_submission = "pending"
        row.save(
            update_fields=[
                "dispatch_backend_kind",
                "dispatch_ciphertext",
                "dispatch_sha256",
                "deployment_workflow_id",
                "deployment_submission",
                "updated_at",
                "version",
            ]
        )
        transaction.on_commit(lambda: dispatch(row.pk))


def payload(row):
    from astrolift_workflows.inputs import Actor, DeployAppInput
    from astrolift_workflows.native_identity_inputs import AcceptedAppIdentityAuthority

    raw = decrypt(EncryptedSecret(row.dispatch_backend_kind, bytes(row.dispatch_ciphertext)))
    if hashlib.sha256(raw).hexdigest() != row.dispatch_sha256:
        raise source.SourceError("DEPLOY_INTENT_CHANGED", "the admitted deployment payload integrity changed")
    data = json.loads(raw)
    value = data["args"][0]
    value["actor"] = Actor(**value["actor"])
    reference = value.get("identity_authority")
    if reference is not None:
        value["identity_authority"] = AcceptedAppIdentityAuthority(
            **{
                **reference,
                "accepted_token_scopes": tuple(reference["accepted_token_scopes"]),
            }
        )
    argument = DeployAppInput(**value)
    if (
        data["workflow_kind"] != "DeployAppWorkflow"
        or data["workflow_id"] != row.deployment_workflow_id
        or data["organization_id"] != row.organization_id
        or argument.deployment_id != row.deployment_id
        or argument.registered_app_id != row.hosted_app_id
        or argument.app_environment_id != row.deployment.app_environment_id
        or asdict(argument.actor) != data["actor"]
        or argument.image_tags != {"app": source._tag(row)}
        or row.deployment.image_digest != row.image_digest
        or data["manifest_sha256"] != hashlib.sha256(row.hosted_app.manifest_raw.encode()).hexdigest()
        or data["cluster_guid"] != str(row.deployment.app_environment.tenant_cluster.guid)
    ):
        raise source.SourceError("DEPLOY_INTENT_CHANGED", "the admitted deployment identity changed")
    return data, [argument]


def _admitted(row, kind):
    from astrolift_lifecycle.deployment_identity_origin import deployment_origin
    from astrolift_services.native_identity_authority import current_app_identity_authority

    with source.authority(row):
        source.validate(row)
        if kind == "build":
            if row.build_first_submission_at is None and row.build_deadline <= timezone.now():
                raise source.SourceError("BUILD_DEADLINE_EXCEEDED", "the build admission deadline elapsed")
            return "BuilderBrainSourceBuildWorkflow", [row.pk], f"BuilderBrainSourceBuildWorkflow-{row.guid}"
        from astrolift_identity.abac import operation_attributes
        from astrolift_identity.operation_context import deployment_approval_count
        from astrolift_lifecycle.models import Deployment
        from astrolift_lifecycle.visibility import live_lifecycle_rows
        from astrolift_registry.scopes import _app_scope
        from core.permissions import Permission, check_permission

        if not live_lifecycle_rows(Deployment.objects.all()).filter(pk=row.deployment_id).exists():
            raise source.SourceError(
                "DEPLOYMENT_UNAVAILABLE", "the original live deployment target is unavailable"
            )
        data, args = payload(row)
        with operation_attributes(
            environment=row.deployment.app_environment.name,
            region=row.deployment.app_environment.tenant_cluster.region or None,
            approvals=deployment_approval_count(row.deployment),
        ):
            check_permission(
                Permission.APP_DEPLOY,
                scope=_app_scope(pk=row.hosted_app_id, permission=Permission.APP_DEPLOY),
            )
        original = deployment_origin(row.deployment)
        if original != args[0].identity_authority:
            raise source.SourceError("DEPLOY_ORIGIN_CHANGED", "the original deployment authority changed")
        if original is not None:
            with current_app_identity_authority(original, deployment_guid=row.deployment.guid):
                pass
        return data["workflow_kind"], args, data["workflow_id"]


def _locked(row_id):
    return (
        BuilderBrainBuild.objects.select_for_update(of=("self",))
        .select_related(
            "organization",
            "project",
            "brain",
            "hosted_app",
            "deployment__app_environment__tenant_cluster",
        )
        .get(pk=row_id)
    )


def _record(row, kind, handle, description):
    if not handle.run_id or (
        getattr(row, kind + "_run_id") and getattr(row, kind + "_run_id") != handle.run_id
    ):
        raise source.SourceError("EXECUTION_CHANGED", "the original execution identity changed")
    setattr(row, kind + "_run_id", handle.run_id)
    setattr(row, kind + "_submission", "enqueued")
    if description is not None:
        if description["run_id"] != handle.run_id or description["workflow_type"] != _admitted(row, kind)[0]:
            raise source.SourceError("EXECUTION_CHANGED", "the observed engine identity changed")
        setattr(row, kind + "_engine_status", description["status"])
    if kind == "deployment" and not row.deployment.workflow_run_id:
        from astrolift_operations.models import WorkflowRun

        data, _ = payload(row)
        status = (description or {}).get("status", "RUNNING").lower()
        if status == "canceled":
            status = "cancelled"
        run = WorkflowRun.objects.create(
            workflow_kind=data["workflow_kind"],
            workflow_id=handle.workflow_id,
            run_id=handle.run_id,
            organization_id=row.organization_id,
            registered_app_id=row.hosted_app_id,
            app_environment_id=row.deployment.app_environment_id,
            trigger_actor_user_id=data["actor"]["user_id"],
            trigger_kind=data["run_trigger"],
            status=status,
            started_at=timezone.now(),
        )
        row.deployment.workflow_run = run
        row.deployment.save(update_fields=["workflow_run", "updated_at", "version"])
    if row.status != "failed":
        row.failure_code = ""
    row.save(
        update_fields=[
            kind + "_run_id",
            kind + "_submission",
            kind + "_engine_status",
            "failure_code",
            "updated_at",
            "version",
        ]
    )
    return row


def _failure(row_id, kind, exc):
    from core.permissions import PermissionDenied

    with transaction.atomic():
        row = _locked(row_id)
        refused = isinstance(exc, (source.SourceError, PermissionDenied))
        setattr(row, kind + "_submission", "refused" if refused else "uncertain")
        row.failure_code = (
            exc.code
            if isinstance(exc, source.SourceError)
            else ("AUTHORITY_REVOKED" if refused else "DISPATCH_UNCERTAIN")
        )
        row.save(update_fields=[kind + "_submission", "failure_code", "updated_at", "version"])
        return row


def dispatch(row_id, kind="deployment", *, recovery_only=False):
    """One elected RPC attempt; all later calls can only recover exact history.

    The attempted marker commits before the RPC. Even after history retention
    expires, an absent execution cannot authorize another start. A crash between
    marker and RPC intentionally requires explicit new review/intent.
    """
    from django.db import connection

    from astrolift_workflows.client import (
        describe_workflow_instance,
        recover_workflow_once,
        start_workflow_once,
    )

    if connection.in_atomic_block:
        # A marker saved only in a nested savepoint is not durable. This also
        # supports installs wrapping HTTP requests in ATOMIC_REQUESTS.
        transaction.on_commit(lambda: dispatch(row_id, kind, recovery_only=recovery_only))
        return BuilderBrainBuild.objects.get(pk=row_id)

    if kind not in {"build", "deployment"}:
        raise ValueError("invalid source execution kind")
    try:
        with transaction.atomic():
            row = _locked(row_id)
            if kind == "deployment" and (
                row.deployment_id is None or row.deployment_submission == "approval_pending"
            ):
                return row
            workflow_kind, args, workflow_id = _admitted(row, kind)
            handle = recover_workflow_once(workflow_kind, args, workflow_id=workflow_id)
            if handle is not None:
                description = describe_workflow_instance(handle.workflow_id, run_id=handle.run_id)
                return _record(row, kind, handle, description)
            if getattr(row, kind + "_first_submission_at") is not None:
                raise source.SourceError(
                    "EXECUTION_RECOVERY_REQUIRED",
                    "the recorded attempt has no retained matching history; reconcile without resubmitting",
                )
            if recovery_only:
                return row
            setattr(row, kind + "_first_submission_at", timezone.now())
            setattr(row, kind + "_submission", "submitting")
            row.save(
                update_fields=[kind + "_first_submission_at", kind + "_submission", "updated_at", "version"]
            )
        # Only this elected caller can reach the sole start RPC. A concurrent
        # request sees the durable marker and performs read-only recovery.
        _admitted(row, kind)
        handle = start_workflow_once(workflow_kind, args, workflow_id=workflow_id)
        description = describe_workflow_instance(handle.workflow_id, run_id=handle.run_id)
        with transaction.atomic():
            row = _locked(row_id)
            _admitted(row, kind)
            return _record(row, kind, handle, description)
    except Exception as exc:
        return _failure(row_id, kind, exc)
