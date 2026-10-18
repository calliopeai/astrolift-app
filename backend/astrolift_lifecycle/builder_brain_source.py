"""Admission and immutable build artifacts for Builder-to-brain hosting (#2456).

No cloud resource creation or identity grants occur here. The operator supplies
an exact project-isolated target; hosting consumes a verified artifact in a new
HTTP request and uses the ordinary brain-binding and deployment mechanisms.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import io
import json
import re
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from astrolift_identity.api_tokens import (
    reset_current_api_token,
    set_current_api_token,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken, Project
from astrolift_lifecycle import builder_artifacts, builder_views
from astrolift_lifecycle.builder_brain_recipe import recipe
from astrolift_lifecycle.models import BuilderBrainBuild, DevEnvironment
from astrolift_registry import brain_apps, expiry
from core.permissions import Permission, PermissionScope, ScopeKind, check_permission
from core.tenancy import TenantContext, tenant_context

PROTOCOL = "builder-brain-source-v1"
ARCHIVE_SALT = "astrolift.builder.brain-build-archive.v1"
LABEL = "astrolift.io/builder-brain-build"
BUILD_SECONDS = 1800
GRANT_SECONDS = 1800


class SourceError(ValueError):
    def __init__(self, code, detail, status=409):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _binding(token):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        ("astrolift.builder-source-credential.v1:" + token.token_hash).encode(),
        hashlib.sha256,
    ).hexdigest()


def _audit(row, action):
    from core.mutations import AuditEntry, emit_audit

    emit_audit(
        AuditEntry(
            actor_user_id=row.actor_id,
            organization_id=row.organization_id,
            action=action,
            decision="ALLOW",
            target_kind="BuilderBrainBuild",
            target_id=str(row.guid),
            duration_ms=0,
            permissions=("app.create", "app.deploy"),
            extra={
                "project": str(row.project.guid),
                "brain": str(row.brain.guid),
                "archive_sha256": row.archive_sha256,
            },
        )
    )


def _deny_response(response):
    if response is not None:
        raise SourceError("PERMISSION_DENIED", "source ownership or permission is unavailable", 403)


def source_owner(token, org, dev):
    if (
        dev.organization_id != org.pk
        or dev.deleted_at is not None
        or dev.status == DevEnvironment.Status.TORN_DOWN
        or (dev.team_id and (dev.team.deleted_at is not None or dev.team.organization_id != org.pk))
    ):
        raise SourceError("NOT_FOUND", "source environment not found", 404)
    _deny_response(
        builder_views._authorize(
            token, org, (Permission.APP_CREATE,), team=dev.team, org_only=dev.team_id is None
        )
    )
    _deny_response(builder_views._owner_denied(token, dev))


def destination(token, org, project_guid, brain_slug):
    project = (
        Project.objects.select_related("team", "organization")
        .filter(
            guid=project_guid,
            deleted_at__isnull=True,
            organization_id=org.pk,
            organization__deleted_at__isnull=True,
            team__organization_id=org.pk,
            team__deleted_at__isnull=True,
        )
        .first()
    )
    if project is None:
        raise SourceError("NOT_FOUND", "project not found", 404)
    if token.organization_id != org.pk or (token.team_id is not None and token.team_id != project.team_id):
        raise SourceError("PERMISSION_DENIED", "destination is outside the credential's team", 403)
    for permission in (Permission.APP_CREATE, Permission.APP_DEPLOY):
        check_permission(permission, scope=PermissionScope(ScopeKind.PROJECT, project.pk))
    brain = brain_apps.readable_brain_for(token.user, org.pk, brain_slug)
    if brain is None or brain.project_id != project.pk or brain.team_id != project.team_id:
        raise SourceError("NOT_FOUND", "readable brain in the selected project not found", 404)
    if not brain_apps.mcp_urls(brain):
        raise SourceError("BRAIN_NOT_SERVED", "selected brain has no served MCP endpoint")
    return project, brain


def configured_target(project):
    raw = getattr(settings, "BUILDER_BRAIN_BUILD_TARGETS", {})
    try:
        targets = json.loads(raw) if isinstance(raw, str) else raw
        target = targets[str(project.guid)]
        required = {
            "organization_guid",
            "cluster_guid",
            "namespace",
            "namespace_uid",
            "service_account",
            "service_account_uid",
            "role_arn",
            "registry_repo_name",
            "registry_repo_uri",
            "attested_project_isolation",
        }
        if (
            not isinstance(target, dict)
            or set(target) != required
            or target["attested_project_isolation"] is not True
        ):
            raise ValueError
        if target["organization_guid"] != str(project.organization.guid):
            raise ValueError
        for key in ("namespace", "service_account"):
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", target[key]):
                raise ValueError
        if not target["namespace_uid"] or not target["service_account_uid"]:
            raise ValueError
        # A configured project must never share its writer identity or repo
        # with another configured project, including another organization.
        for guid, other in targets.items():
            if guid != str(project.guid) and (
                other["registry_repo_uri"] == target["registry_repo_uri"]
                or other["role_arn"] == target["role_arn"]
                or (other["cluster_guid"], other["namespace"], other["service_account"])
                == (target["cluster_guid"], target["namespace"], target["service_account"])
            ):
                raise ValueError
        from astrolift_clusters.models import TenantCluster

        cluster = (
            TenantCluster.objects.select_related("provider_plugin")
            .filter(
                guid=target["cluster_guid"],
                deleted_at__isnull=True,
                is_active=True,
                lifecycle=TenantCluster.Lifecycle.MANAGED.value,
                provider_plugin__slug="aws",
                provider_plugin__deleted_at__isnull=True,
                provider_plugin__is_enabled=True,
            )
            .filter(Q(organization_id=project.organization_id) | Q(organization__isnull=True))
            .first()
        )
        if cluster is None:
            raise ValueError
        pc = cluster.provider_config or {}
        account = str(pc.get("account_id", ""))
        region = str(pc.get("region", cluster.region or ""))
        # Read existing configuration only; no EKS/OIDC discovery or writes.
        if (
            not re.fullmatch(r"[0-9]{12}", account)
            or not region
            or not (cluster.auth_config or {}).get("cluster_oidc_issuer")
        ):
            raise ValueError
        uri = f"{account}.dkr.ecr.{region}.amazonaws.com/{target['registry_repo_name']}"
        if target["registry_repo_uri"] != uri or not re.fullmatch(
            r"[a-z0-9][a-z0-9._/-]{0,254}", target["registry_repo_name"]
        ):
            raise ValueError
        if not re.fullmatch(rf"arn:aws:iam::{account}:role/[A-Za-z0-9+=,.@_/-]+", target["role_arn"]):
            raise ValueError
        return dict(target), cluster
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SourceError(
            "BUILD_TARGET_UNAVAILABLE", "an exact isolated immutable project build target must be configured"
        ) from None


def observe_target(project, expected=None):
    from core.app_deploy import driver_for_capability
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    target, cluster = configured_target(project)
    if expected is not None and target != expected:
        raise SourceError("BUILD_TARGET_CHANGED", "the admitted build target configuration changed")
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    namespace = driver.get_manifest(ctx.slug, None, "Namespace", target["namespace"])
    if str((namespace or {}).get("metadata", {}).get("uid", "")) != target["namespace_uid"]:
        raise SourceError("BUILD_NAMESPACE_CHANGED", "the configured build namespace identity is unavailable")
    sa = driver.get_manifest(ctx.slug, target["namespace"], "ServiceAccount", target["service_account"])
    metadata = (sa or {}).get("metadata", {})
    if (
        str(metadata.get("uid", "")) != target["service_account_uid"]
        or metadata.get("annotations", {}).get("eks.amazonaws.com/role-arn") != target["role_arn"]
        or metadata.get("deletionTimestamp")
    ):
        raise SourceError(
            "BUILD_IDENTITY_CHANGED", "the configured build service account identity is unavailable"
        )
    registry = driver_for_capability(cluster, "registry")
    observed = registry.observe_build_repository(target["registry_repo_name"])
    if (
        observed.name != target["registry_repo_name"]
        or observed.uri != target["registry_repo_uri"]
        or not observed.immutable_tags
    ):
        raise SourceError(
            "BUILD_REPOSITORY_CHANGED", "the configured repository must exist with strict immutable tags"
        )
    return target, cluster, driver, ctx, registry


def source_descriptor(dev):
    raw = builder_artifacts.pack(dev)
    return {
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "archive_bytes": len(raw),
        "expanded_bytes": sum(e.file_size for e in zipfile.ZipFile(io.BytesIO(raw)).infolist()),
    }, raw


def admit(request, token, org, dev_guid, body):
    allowed = {
        "project_guid",
        "brain_slug",
        "app_name",
        "app_slug",
        "idempotency_key",
        "expected_archive_sha256",
        "expires_in",
        "expires_at",
        "delete_data_on_expiry",
    }
    if set(body) - allowed or any(
        not isinstance(body.get(k), str) or not body[k].strip()
        for k in allowed - {"expires_in", "expires_at", "delete_data_on_expiry"}
    ):
        raise SourceError(
            "VALIDATION",
            "exact project, brain, name, slug, idempotency key and expected archive digest are required",
            400,
        )
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", body["idempotency_key"]) or not re.fullmatch(
        r"[a-f0-9]{64}", body["expected_archive_sha256"]
    ):
        raise SourceError("VALIDATION", "invalid idempotency key or archive digest", 400)
    if (
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", body["app_slug"])
        or len(body["app_name"]) > 200
    ):
        raise SourceError("VALIDATION", "invalid app slug or name", 400)
    if type(body.get("delete_data_on_expiry")) is not bool:
        raise SourceError("VALIDATION", "delete_data_on_expiry must be an explicit boolean", 400)
    try:
        project_guid = str(UUID(body["project_guid"]))
        expires_at = dt.datetime.fromisoformat(body["expires_at"]) if body.get("expires_at") else None
        if body.get("expires_in") is not None and not isinstance(body["expires_in"], str):
            raise ValueError
    except (ValueError, TypeError):
        raise SourceError("VALIDATION", "invalid project GUID or expiry", 400) from None
    with transaction.atomic():
        from django.db import connection

        # Serialize the idempotency key even when two different source rows
        # race; the row lock below alone cannot protect that collision.
        key = int.from_bytes(
            hashlib.sha256(
                f"builder.brain-source.v1:{org.pk}:{token.user_id}:{body['idempotency_key']}".encode()
            ).digest()[:8],
            "big",
            signed=True,
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
        dev = (
            DevEnvironment.objects.select_for_update(of=("self",))
            .select_related("team")
            .filter(guid=dev_guid, organization_id=org.pk)
            .first()
        )
        if dev is None:
            raise SourceError("NOT_FOUND", "source environment not found", 404)
        source_owner(token, org, dev)
        project, brain = destination(token, org, project_guid, body["brain_slug"])
        existing = BuilderBrainBuild.objects.filter(
            organization_id=org.pk, actor_id=token.user_id, idempotency_key=body["idempotency_key"]
        ).first()
        digest = _sha({"dev": str(dev.guid), **body})
        if existing is not None:
            if existing.request_sha256 != digest or existing.dev_environment_id != dev.pk:
                raise SourceError(
                    "IDEMPOTENCY_CONFLICT", "the key already identifies a different source or hosting request"
                )
            validate(existing)
            return existing, False
        from astrolift_registry.brain_manage import surface

        with surface("chat_studio"):
            when = expiry.resolve(org, expires_in=body.get("expires_in"), expires_at=expires_at)
            if when is None and (reason := expiry.permanent_refusal(org)):
                raise SourceError("EXPIRY_REQUIRED", reason, 400)
        generated = recipe(dev)
        descriptor, raw = source_descriptor(dev)
        if descriptor["archive_sha256"] != body["expected_archive_sha256"]:
            raise SourceError(
                "SOURCE_CHANGED", "source changed; read its current archive digest before retrying"
            )
        target, _, _, _, _ = observe_target(project)
        key = f"builder/{org.pk}/{dev.guid}/{descriptor['archive_sha256']}.zip"
        store = builder_artifacts.storage()
        if not store.exists(key):
            saved = store.save(key, ContentFile(raw))
            if saved != key:
                raise SourceError(
                    "ARCHIVE_STORAGE_CONFLICT", "the private archive store did not preserve its immutable key"
                )
        row = BuilderBrainBuild.objects.create(
            organization=org,
            project=project,
            brain=brain,
            dev_environment=dev,
            actor_id=token.user_id,
            credential=token,
            credential_binding=_binding(token),
            accepted_scopes=sorted(set(token.scopes)),
            idempotency_key=body["idempotency_key"],
            request_sha256=digest,
            archive_key=key,
            archive_sha256=descriptor["archive_sha256"],
            archive_bytes=descriptor["archive_bytes"],
            expanded_bytes=descriptor["expanded_bytes"],
            admission={
                "source_creator_id": dev.creator_id,
                "source_team_id": dev.team_id,
                "credential_team_id": token.team_id,
                "credential_expires_at": token.expires_at.isoformat() if token.expires_at else None,
                "target": target,
                "project_team_id": project.team_id,
                "app_slug": body["app_slug"],
                "app_name": body["app_name"],
                "port": dev.port,
                "env_vars": dev.env_vars,
                "data_file_path": dev.data_file_path,
                "expires_in": body.get("expires_in"),
                "expires_at": body.get("expires_at"),
                "delete_data_on_expiry": body["delete_data_on_expiry"],
                **generated,
            },
            build_deadline=timezone.now() + dt.timedelta(seconds=BUILD_SECONDS + 300),
        )
        _audit(row, "builder.brain_source.admit")
        return row, True


@contextmanager
def authority(row):
    token = with_active_org_member(
        ApiToken.objects.filter(
            pk=row.credential_id,
            user_id=row.actor_id,
            organization_id=row.organization_id,
            user__is_active=True,
            is_revoked=False,
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())),
        user="user",
        organization="organization",
    ).first()
    if (
        token is None
        or not hmac.compare_digest(_binding(token), row.credential_binding)
        or sorted(set(token.scopes)) != row.accepted_scopes
        or token.team_id != row.admission["credential_team_id"]
        or (token.expires_at.isoformat() if token.expires_at else None)
        != row.admission["credential_expires_at"]
    ):
        raise SourceError(
            "ORIGINAL_CREDENTIAL_REVOKED",
            "the original build credential or its accepted ceiling is no longer current",
            403,
        )
    from astrolift_identity import abac
    from core.permissions import _scopes_memo

    current = abac.current_attributes()
    # A worker can inherit a context from a prior check. Never reuse its
    # policy/group cache or evaluation clock across effects. Only a genuine
    # current HTTP request may supply person-bound request facts.
    attrs = (
        replace(current, cache={}, now=None)
        if current is not None and current.actor_user_id == row.actor_id and current.request is not None
        else abac.RequestAttributes(actor_user_id=row.actor_id)
    )
    marker = set_current_api_token(token)
    scopes_marker = _scopes_memo.set({})
    try:
        with (
            tenant_context(TenantContext(organization_id=row.organization_id, actor_user_id=row.actor_id)),
            abac.request_attributes(attrs),
        ):
            yield token
    finally:
        _scopes_memo.reset(scopes_marker)
        reset_current_api_token(marker)


def validate(row):
    with authority(row) as token:
        dev = (
            DevEnvironment.objects.select_related("team")
            .filter(pk=row.dev_environment_id, organization_id=row.organization_id)
            .first()
        )
        if (
            dev is None
            or dev.creator_id != row.admission["source_creator_id"]
            or dev.team_id != row.admission["source_team_id"]
        ):
            raise SourceError("SOURCE_OWNER_CHANGED", "the admitted source owner changed", 403)
        source_owner(token, row.organization, dev)
        project, brain = destination(token, row.organization, str(row.project.guid), row.brain.slug)
        if project.team_id != row.admission["project_team_id"] or brain.pk != row.brain_id:
            raise SourceError("DESTINATION_CHANGED", "the admitted destination ownership changed", 403)
        return project


def _tag(row):
    return "intent-" + UUID(str(row.guid)).hex


def _verified_image(row, registry):
    from astrolift_workflows.activities.image_digest import is_digest

    found = [
        tag.digest
        for tag in registry.list_tags(row.admission["target"]["registry_repo_name"])
        if tag.name == _tag(row)
    ]
    if len(found) != 1 or not is_digest(found[0]):
        return ""
    if row.image_digest and row.image_digest != found[0]:
        raise SourceError(
            "IMAGE_DIGEST_CHANGED", "the immutable build tag no longer identifies the admitted image"
        )
    return found[0]


def cleanup_build_objects(row, driver, ctx):
    """Only exact originally observed Job/Secret UIDs, never a namespace or repo."""
    target = row.admission["target"]
    namespace = driver.get_manifest(ctx.slug, None, "Namespace", target["namespace"])
    if str((namespace or {}).get("metadata", {}).get("uid", "")) != target["namespace_uid"]:
        raise SourceError("CLEANUP_NAMESPACE_CHANGED", "build cleanup refused a replacement namespace")
    receipt = dict(row.cleanup_receipt)
    for key, ref in list(receipt.items()):
        if ref.get("deleted"):
            continue
        current = driver.get_manifest(ctx.slug, target["namespace"], ref["kind"], ref["name"])
        if current is not None:
            metadata = current.get("metadata", {})
            if str(metadata.get("uid", "")) != ref["uid"] or metadata.get("labels", {}).get(LABEL) != str(
                row.guid
            ):
                raise SourceError("CLEANUP_RESOURCE_CHANGED", "build cleanup refused a replacement object")
            result = driver.delete_manifests(
                ctx.slug,
                target["namespace"],
                [
                    {
                        "apiVersion": ref["apiVersion"],
                        "kind": ref["kind"],
                        "metadata": {
                            "name": ref["name"],
                            "namespace": target["namespace"],
                            "uid": ref["uid"],
                        },
                    }
                ],
            )
            if not result.ok:
                raise SourceError("BUILD_CLEANUP_PENDING", "owned build object deletion is not confirmed")
            if driver.get_manifest(ctx.slug, target["namespace"], ref["kind"], ref["name"]) is not None:
                raise SourceError("BUILD_CLEANUP_PENDING", "owned build object is still present")
        receipt[key] = dict(ref, deleted=True)
        row.cleanup_receipt = receipt
        row.save(update_fields=["cleanup_receipt", "updated_at", "version"])


def build(row_id):
    """Real native driver orchestration. PostgreSQL serializes activity retries."""
    from _sdk.build import BuildContextArchive, BuildSpec
    from django.db import connection
    from k8s_native.build_kaniko import KanikoBuildDriver

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [2456, row_id])
        locked = cursor.fetchone()[0]
    if not locked:
        raise SourceError("BUILD_ALREADY_RUNNING", "the admitted build is already running")
    row = None
    try:
        row = BuilderBrainBuild.objects.select_related("organization", "project", "brain").get(pk=row_id)
        project = validate(row)
        target, _, driver, ctx, registry = observe_target(project, row.admission["target"])
        if row.hosted_app_id:
            return {"ok": True, "status": row.status}
        # Recover a verified push from an interrupted activity before running
        # another job. The tag is immutable and belongs to this intent only.
        digest = _verified_image(row, registry)
        if digest and (
            row.image_digest != digest
            or not any(
                ref.get("kind") == "Job" and ref.get("completed_image_digest") == digest
                for ref in row.cleanup_receipt.values()
            )
        ):
            raise SourceError(
                "IMAGE_PROVENANCE_UNCONFIRMED",
                "an existing tag without this intent's completed owned build receipt cannot be adopted",
            )
        cleanup_build_objects(row, driver, ctx)
        if not digest:
            if row.build_deadline <= timezone.now():
                raise SourceError(
                    "BUILD_DEADLINE_EXCEEDED", "the bounded build deadline elapsed; create a new intent"
                )
            row.status, row.failure_code = "building", ""
            row.save(update_fields=["status", "failure_code", "updated_at", "version"])
            grant = signing.dumps(
                {"intent": str(row.guid), "org": row.organization_id, "sha256": row.archive_sha256},
                salt=ARCHIVE_SALT,
            )
            base = builder_artifacts.reference_base_url()
            archive = BuildContextArchive(
                url=base + f"/api/builder/v1/brain-source-builds/{row.guid}/archive/",
                token=grant,
                sha256=row.archive_sha256,
                size_bytes=row.archive_bytes,
                expanded_bytes=row.expanded_bytes,
                dockerfile=row.admission["dockerfile"],
                entrypoint=row.admission["entrypoint"],
            )

            def guard(manifests):
                validate(row)
                observe_target(project, target)
                if row.build_deadline <= timezone.now():
                    raise SourceError("BUILD_DEADLINE_EXCEEDED", "the bounded build deadline elapsed")
                for manifest in manifests:
                    metadata = manifest["metadata"]
                    metadata.setdefault("labels", {})[LABEL] = str(row.guid)
                    if manifest["kind"] == "Job":
                        manifest["spec"]["template"]["metadata"]["labels"][LABEL] = str(row.guid)
                    if (
                        driver.get_manifest(ctx.slug, target["namespace"], manifest["kind"], metadata["name"])
                        is not None
                    ):
                        raise SourceError(
                            "BUILD_OBJECT_OCCUPIED",
                            "a build object name is occupied without a current ownership receipt",
                        )

            def observed(manifests, result):
                receipt = dict(row.cleanup_receipt)
                for manifest in manifests:
                    kind, name = manifest["kind"], manifest["metadata"]["name"]
                    key = f"{kind}/{name}"
                    if key not in result.created:
                        continue
                    current = driver.get_manifest(ctx.slug, target["namespace"], kind, name)
                    metadata = (current or {}).get("metadata", {})
                    uid = metadata.get("uid")
                    if not uid or metadata.get("labels", {}).get(LABEL) != str(row.guid):
                        raise SourceError(
                            "BUILD_OBSERVATION_UNCONFIRMED", "created object ownership could not be observed"
                        )
                    receipt[key] = {
                        "apiVersion": manifest["apiVersion"],
                        "kind": kind,
                        "name": name,
                        "uid": str(uid),
                        "deleted": False,
                    }
                    row.cleanup_receipt = receipt
                    row.save(update_fields=["cleanup_receipt", "updated_at", "version"])

            builder = KanikoBuildDriver(
                cluster_driver=driver,
                cluster_slug=ctx.slug,
                namespace=target["namespace"],
                service_account=target["service_account"],
                manage_service_account=False,
                build_id=str(row.guid),
                timeout_seconds=min(BUILD_SECONDS, (row.build_deadline - timezone.now()).total_seconds()),
                manifest_guard=guard,
                manifest_observer=observed,
            )
            try:
                result = builder.build(
                    BuildSpec(source_uri="builder-archive:" + row.archive_sha256, context_archive=archive),
                    target["registry_repo_uri"],
                    _tag(row),
                )
                if not result.success:
                    raise SourceError(
                        "BUILD_FAILED",
                        "native build failed; dependency installation and registry push must succeed",
                    )
                digest = _verified_image(row, registry)
                if not digest:
                    raise SourceError(
                        "IMAGE_UNVERIFIED", "the registry did not confirm the build's immutable image digest"
                    )
                job_receipts = [
                    key
                    for key, ref in row.cleanup_receipt.items()
                    if ref["kind"] == "Job" and not ref["deleted"]
                ]
                if len(job_receipts) != 1:
                    raise SourceError(
                        "IMAGE_PROVENANCE_UNCONFIRMED", "the successful build has no exact owned Job receipt"
                    )
                receipt = row.cleanup_receipt[job_receipts[0]]
                completed = driver.get_manifest(ctx.slug, target["namespace"], "Job", receipt["name"])
                metadata = (completed or {}).get("metadata", {})
                succeeded = (completed or {}).get("status", {}).get("succeeded", 0)
                if (
                    str(metadata.get("uid", "")) != receipt["uid"]
                    or metadata.get("labels", {}).get(LABEL) != str(row.guid)
                    or not isinstance(succeeded, int)
                    or isinstance(succeeded, bool)
                    or succeeded < 1
                ):
                    raise SourceError(
                        "IMAGE_PROVENANCE_UNCONFIRMED",
                        "the exact observed owned build Job has not confirmed successful completion",
                    )
                receipt["completed_image_digest"] = digest
                row.image_digest = digest
                row.image_ref = target["registry_repo_uri"] + "@" + digest
                row.save(
                    update_fields=["cleanup_receipt", "image_digest", "image_ref", "updated_at", "version"]
                )
            finally:
                cleanup_build_objects(row, driver, ctx)
        validate(row)
        row.image_digest = digest
        row.image_ref = target["registry_repo_uri"] + "@" + digest
        row.status, row.failure_code = "artifact_ready", ""
        row.save(
            update_fields=["image_digest", "image_ref", "status", "failure_code", "updated_at", "version"]
        )
        _audit(row, "builder.brain_source.artifact_ready")
        return {"ok": True, "status": row.status}
    except Exception as exc:
        code = exc.code if isinstance(exc, SourceError) else "BUILD_NATIVE_FAILURE"
        if row is not None and not row.hosted_app_id:
            row.status, row.failure_code = "failed", code
            row.save(update_fields=["status", "failure_code", "updated_at", "version"])
        # Driver/HTTP errors may contain credentials or uploaded source. Only a
        # fixed metadata code crosses Temporal's activity/history boundary.
        raise RuntimeError(code) from None
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [2456, row_id])


def host(request, token, org, row_guid, expected_image_digest):
    from astrolift_graphql import GUID
    from astrolift_lifecycle.builder_brain_dispatch import Capture, dispatch
    from astrolift_lifecycle.builder_brain_recipe import manifest
    from astrolift_lifecycle.models import Deployment
    from astrolift_lifecycle.schema.mutations.deployments import DeploymentMutations
    from astrolift_lifecycle.schema.mutations.helpers import source_deployment_dispatch
    from astrolift_lifecycle.schema.mutations.types import StartDeploymentInput
    from astrolift_registry.brain_manage import surface
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.schema.mutations.brains import HostAppOnBrainInput, host_app_record

    with transaction.atomic():
        row = (
            BuilderBrainBuild.objects.select_for_update(of=("self",))
            .select_related(
                "organization",
                "project",
                "brain",
                "hosted_app",
                "deployment",
            )
            .filter(guid=row_guid, organization_id=org.pk, actor_id=token.user_id, credential_id=token.pk)
            .first()
        )
        if row is None:
            raise SourceError("NOT_FOUND", "source build not found", 404)
        project = validate(row)
        if row.hosted_app_id:
            from astrolift_lifecycle.visibility import live_app_rows

            if (
                not live_app_rows(RegisteredApp.objects.all(), org_id=org.pk)
                .filter(pk=row.hosted_app_id, project_id=project.pk)
                .exists()
            ):
                raise SourceError(
                    "HOSTED_APP_UNAVAILABLE",
                    "the original hosted app is no longer live; the intent cannot create a replacement",
                )
            if expected_image_digest != row.image_digest:
                raise SourceError("IMAGE_DIGEST_CHANGED", "expected_image_digest does not match this build")
            if row.dispatch_ciphertext:
                transaction.on_commit(lambda: dispatch(row.pk))
            return row
        if row.status != "artifact_ready" or expected_image_digest != row.image_digest:
            raise SourceError(
                "ARTIFACT_NOT_READY", "hosting requires this intent's verified artifact_ready image digest"
            )
        _, cluster, _, _, registry = observe_target(project, row.admission["target"])
        digest = _verified_image(row, registry)
        if not digest or row.image_ref != row.admission["target"]["registry_repo_uri"] + "@" + digest:
            raise SourceError(
                "IMAGE_UNVERIFIED", "the registry no longer confirms this intent's immutable image"
            )
        info = SimpleNamespace(context=SimpleNamespace(request=request, user=request.user))
        initial = row.admission
        expires_at = dt.datetime.fromisoformat(initial["expires_at"]) if initial["expires_at"] else None
        # No surrogate HTTP request, native receipt or system deployer is
        # synthesized. The real fresh request reaches ordinary deploy admission.
        with surface("chat_studio"):
            result = host_app_record(
                info,
                HostAppOnBrainInput(
                    project_id=GUID(str(project.guid)),
                    brain_slug=row.brain.slug,
                    image=row.image_ref,
                    name=initial["app_name"],
                    slug=initial["app_slug"],
                    port=initial["port"],
                    expires_in=initial["expires_in"],
                    expires_at=expires_at,
                    delete_data_on_expiry=initial["delete_data_on_expiry"],
                ),
                cluster=cluster,
                manifest_raw=manifest(
                    app_slug=initial["app_slug"],
                    brain_slug=row.brain.slug,
                    image=row.image_ref,
                    admission=initial,
                ),
            )
            if not result.ok or result.data is None:
                raise SourceError(
                    "HOST_ADMISSION_REFUSED",
                    "normal brain hosting admission refused the request; check current expiry policy and destination permissions",
                    403,
                )
            app = RegisteredApp.objects.get(guid=result.data.app.id, organization_id=org.pk, project=project)
            with source_deployment_dispatch(Capture(row)):
                deployed = DeploymentMutations().start_deployment(
                    info,
                    input=StartDeploymentInput(
                        app_slug=app.slug,
                        environment_name="production",
                        image_tag=_tag(row),
                        image_digest=digest,
                        trigger_kind="manual",
                    ),
                )
            if not deployed.ok or deployed.data is None:
                raise SourceError(
                    "DEPLOY_ADMISSION_REFUSED",
                    "normal deployment admission refused the request; no app or binding was committed",
                    403,
                )
        row.hosted_app = app
        row.deployment = Deployment.objects.get(guid=deployed.data.id, registered_app=app)
        row.status = "hosted"
        if row.deployment.status == "pending_approval":
            row.deployment_submission = "approval_pending"
        row.save(
            update_fields=[
                "hosted_app",
                "deployment",
                "status",
                "deployment_submission",
                "updated_at",
                "version",
            ]
        )
        _audit(row, "builder.brain_source.host")
        return row


def response(row):
    app, deployment = row.hosted_app, row.deployment
    return {
        "id": str(row.guid),
        "protocol": PROTOCOL,
        "status": row.status,
        "build_submission": row.build_submission,
        "build_execution_status": row.build_engine_status or None,
        "archive_sha256": row.archive_sha256,
        "project_guid": str(row.project.guid),
        "brain_guid": str(row.brain.guid),
        "brain_slug": row.brain.slug,
        "image_digest": row.image_digest or None,
        "image_ref": row.image_ref or None,
        "failure_code": row.failure_code or None,
        "app_guid": str(app.guid) if app else None,
        "app_slug": app.slug if app else None,
        "deployment_guid": str(deployment.guid) if deployment else None,
        "deployment_status": deployment.status if deployment else None,
        "hosting_status": "created" if app else "not_created",
        "deployment_submission": row.deployment_submission,
        "deployment_execution_status": row.deployment_engine_status or None,
        "provisioning_status": app.provisioning_status if app else None,
        "runtime_url": (deployment.app_environment.url or None) if deployment else None,
        "expires_at": app.expires_at.isoformat() if app and app.expires_at else None,
        "delete_data_on_expiry": app.delete_data_on_expiry if app else row.admission["delete_data_on_expiry"],
        "data_persistent": False if row.admission["data_file_path"] else None,
        "runtime_verified": bool(deployment and deployment.status == "running"),
    }
