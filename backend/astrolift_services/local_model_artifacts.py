"""Admin-admitted import and immutable delivery, without pipeline or legacy-org rows."""

import hashlib
import json
import os
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_identity.scopes import identity_organization_scope
from astrolift_services.models.local_model_artifact import LocalModelArtifact
from core.permissions import Permission, PermissionDenied, check_permission
from core.tenancy import get_current_tenant
from providers._sdk.local_model_artifact import (
    ArtifactStoreUnavailable,
    ModelFile,
    VersionedModelStore,
    model_manifest,
    model_object_key,
)


def import_authority(org_guid=None):
    from astrolift_services.cluster_models import cluster_model_org_scope
    from astrolift_services.hosting_authority import current_host_operator

    tenant = get_current_tenant()
    with current_host_operator():
        check_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE)({}))
        check_permission(
            Permission.CLUSTER_UPDATE, scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE)({})
        )
    org = Organization.objects.filter(pk=tenant.organization_id).first()
    if org is None or org_guid is not None and str(org.guid) != str(org_guid):
        raise ValueError("Model source organization is unavailable.")
    return org


def install_model_store(*, checkpoint=lambda: None):
    """Use the existing install bucket only; do not follow mutable per-org fallbacks."""
    checkpoint()
    bucket = str(getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or "")
    region = str(
        getattr(settings, "AWS_S3_REGION_NAME", "")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    endpoint = str(getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "")
    if not bucket or not region or endpoint and not endpoint.startswith("https://"):
        raise ArtifactStoreUnavailable("Local model imports require configured HTTPS install S3 storage.")
    source = hashlib.sha256(
        json.dumps({"bucket": bucket, "region": region, "endpoint": endpoint}, sort_keys=True).encode()
    ).hexdigest()
    import boto3
    from botocore.config import Config

    config = Config(
        connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 1}, signature_version="s3v4"
    )
    session = boto3.Session()
    session._session.set_default_client_config(config)
    session._session.set_config_variable("metadata_service_timeout", 5)
    session._session.set_config_variable("metadata_service_num_attempts", 1)
    refusal = None

    def guarded_checkpoint():
        nonlocal refusal
        if refusal is not None:
            raise refusal
        try:
            checkpoint()
        except Exception as error:
            refusal = error
            raise

    resolver = session._session.get_component("credential_provider")
    for provider in resolver.providers:
        fetcher = getattr(provider, "_fetcher", None) or getattr(provider, "_role_fetcher", None)
        transport = getattr(fetcher, "_session", None)
        if transport is not None:
            original_send = transport.send

            def guarded_send(request, *, send=original_send):
                guarded_checkpoint()
                return send(request)

            transport.send = guarded_send

    def before_send(**_kwargs):
        guarded_checkpoint()

    session._session.register("before-send", before_send)
    try:
        client = session.client("s3", region_name=region, endpoint_url=endpoint or None, config=config)
    except Exception:
        guarded_checkpoint()
        raise ArtifactStoreUnavailable("Local model storage client is unavailable.") from None
    parameters = getattr(settings, "AWS_S3_OBJECT_PARAMETERS", {}) or {}
    return VersionedModelStore(
        client=client,
        bucket=bucket,
        kms_key_id=parameters.get("SSEKMSKeyId", ""),
        checkpoint=guarded_checkpoint,
    ), source


def begin_artifact(*, organization_id, name, files):
    org = import_authority(organization_id)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128 or any(ord(char) < 32 for char in name):
        raise ValueError("Model source name must contain 1 to 128 visible characters.")
    manifest, digest = model_manifest([ModelFile(**file) for file in files])
    store, source = install_model_store(checkpoint=lambda: import_authority(organization_id))
    store.require_versioning()
    with transaction.atomic():
        org = import_authority(organization_id)
        tenant = get_current_tenant()
        if tenant is None:
            scope = identity_organization_scope(Permission.ORG_UPDATE)({})
            raise PermissionDenied(Permission.ORG_UPDATE, scope, "Model source authority is unavailable.")
        artifact = LocalModelArtifact.objects.create(
            organization=org,
            name=name.strip(),
            manifest=manifest,
            manifest_sha256=digest,
            storage_source=source,
            storage_receipt={file["name"]: {"file_id": str(uuid4())} for file in manifest},
            created_by_id=tenant.actor_user_id,
        )
    return artifact


def _artifact(artifact_id, expected_version, *, locked=False):
    org = import_authority()
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
        raise ValueError("Local model source requires its reviewed version.")
    rows = LocalModelArtifact.objects.select_for_update() if locked else LocalModelArtifact.objects
    artifact = rows.filter(guid=UUID(str(artifact_id)), organization=org).first()
    if locked:
        # A waited-on source lock must not retain withdrawn hosting authority.
        import_authority(org.guid)
    if artifact is None:
        raise ValueError("Local model source is unavailable.")
    if artifact.version != expected_version:
        raise ValueError("Local model source changed; refresh before continuing.")
    return artifact


def _store(artifact, checkpoint):
    store, source = install_model_store(checkpoint=checkpoint)
    if source != artifact.storage_source:
        raise ArtifactStoreUnavailable("Local model storage source changed after import acceptance.")
    store.require_versioning()
    return store


def _key(artifact, file):
    return model_object_key(
        str(artifact.organization.guid), str(artifact.guid), artifact.storage_receipt[file["name"]]["file_id"]
    )


def artifact_uploads(artifact_id, expected_version):
    artifact = _artifact(artifact_id, expected_version)
    if artifact.state != LocalModelArtifact.State.UPLOADING:
        raise ValueError("A verified source cannot issue further write grants.")

    def check():
        return _artifact(artifact_id, expected_version)

    store = _store(artifact, check)
    uploads = []
    for file in artifact.manifest:
        url, headers = store.upload(_key(artifact, file), file)
        uploads.append(
            {"name": file["name"], "url": url, "headers": headers, "size_bytes": file["size_bytes"]}
        )
    check()
    return artifact, uploads


def finalize_artifact(artifact_id, expected_version):
    artifact = _artifact(artifact_id, expected_version)
    if artifact.state == LocalModelArtifact.State.VERIFIED:
        return artifact

    def check():
        return _artifact(artifact_id, expected_version)

    store = _store(artifact, check)
    receipt = {}
    for file in artifact.manifest:
        receipt[file["name"]] = {
            "file_id": artifact.storage_receipt[file["name"]]["file_id"],
            "version_id": store.verified_version(_key(artifact, file), file),
        }
    with transaction.atomic():
        current = _artifact(artifact_id, expected_version, locked=True)
        if current.manifest != artifact.manifest or current.storage_receipt != artifact.storage_receipt:
            raise ValueError("Local model source changed during verification.")
        current.storage_receipt, current.state, current.verified_at = (
            receipt,
            LocalModelArtifact.State.VERIFIED,
            timezone.now(),
        )
        current.save(update_fields=["storage_receipt", "state", "verified_at", "updated_at", "version"])
        return current


def validate_artifact_request(org_id, artifact_id, expected_version, *, locked=False):
    """Current source admission; import verification does not assert model fit or health."""
    row = _artifact(artifact_id, expected_version, locked=locked)
    if row.organization_id != org_id or row.state != LocalModelArtifact.State.VERIFIED:
        raise ValueError("Local model source has not completed immutable verification.")
    _, digest = model_manifest([ModelFile(**file) for file in row.manifest])
    if digest != row.manifest_sha256:
        raise ValueError("Local model manifest differs from its admitted source.")
    return {
        "model_source": "local_artifact",
        "model_artifact_id": str(row.guid),
        "model_artifact_version": row.version,
        "model_artifact_manifest_sha256": digest,
        "model": "local-" + str(row.guid),
        "cache_size": f"{max(1, (sum(file['size_bytes'] for file in row.manifest) + 1024**3 - 1) // 1024**3) + 1}Gi",
    }


def prepare_artifact_delivery(service, *, checkpoint):
    """Worker-owned URL generation; the returned private plan must never enter history."""
    cfg = service.config
    checkpoint()
    row = LocalModelArtifact.objects.filter(
        guid=cfg.get("model_artifact_id"),
        organization_id=service.organization_id,
        organization__deleted_at__isnull=True,
        version=cfg.get("model_artifact_version"),
    ).first()
    if (
        row is None
        or row.state != LocalModelArtifact.State.VERIFIED
        or row.manifest_sha256 != cfg.get("model_artifact_manifest_sha256")
    ):
        raise ValueError("Local model source is no longer available.")
    _, digest = model_manifest([ModelFile(**file) for file in row.manifest])
    if digest != row.manifest_sha256:
        raise ValueError("Local model manifest differs from its admitted source.")

    def current():
        checkpoint()
        if not LocalModelArtifact.objects.filter(
            pk=row.pk,
            guid=row.guid,
            organization_id=service.organization_id,
            manifest_sha256=row.manifest_sha256,
            manifest=row.manifest,
            storage_receipt=row.storage_receipt,
            version=row.version,
            state="verified",
            organization__deleted_at__isnull=True,
        ).exists():
            raise ValueError("Local model source changed during delivery.")

    store = _store(row, current)
    files = [
        {
            **file,
            "url": store.download(_key(row, file), file, row.storage_receipt[file["name"]]["version_id"]),
        }
        for file in row.manifest
    ]
    current()
    return {
        "organization_id": str(row.organization.guid),
        "cluster_id": str(service.tenant_cluster.guid),
        "managed_service_id": str(service.guid),
        "artifact_id": str(row.guid),
        "artifact_version": row.version,
        "manifest_sha256": digest,
        "files": files,
    }
