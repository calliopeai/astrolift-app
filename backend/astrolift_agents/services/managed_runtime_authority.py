import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from astrolift_agents.models import AgentBox, ManagedBoxRuntime
from astrolift_operations.models import AuditEvent


class RuntimeAuthorityError(ValueError):
    pass


def runtime_token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def certify_managed_runtime(
    *, box_id, cluster, workspace_path, job_uid, claim_name, claim_uid, pod_name, pod_uid, container_name
):
    """Internal provisioner boundary; never accept these fields from a receiver."""
    api_url = getattr(settings, "PLATFORM_API_URL", "").rstrip("/")
    parsed = urlsplit(api_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeAuthorityError("A canonical HTTPS platform URL is required.")
    if (
        not isinstance(workspace_path, str)
        or not re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", workspace_path)
        or workspace_path == "/state"
        or workspace_path.startswith("/state/")
    ):
        raise RuntimeAuthorityError("A separate absolute workspace path is required.")
    with transaction.atomic():
        box = AgentBox.objects.select_for_update().get(pk=box_id)
        if (
            box.status not in (AgentBox.Status.PROVISIONING, AgentBox.Status.RUNNING)
            or box.organization.deleted_at is not None
            or not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", box.image)
        ):
            raise RuntimeAuthorityError("A live box with an immutable image is required.")
        if ManagedBoxRuntime.all_objects.filter(box=box).exists():
            raise RuntimeAuthorityError("The box already has a runtime owner epoch.")
        token = "alft_box_" + secrets.token_urlsafe(32)
        runtime = ManagedBoxRuntime(
            box=box,
            cluster=cluster,
            api_url=api_url,
            workspace_path=workspace_path,
            namespace=box.namespace,
            job_name=box.external_id,
            job_uid=job_uid,
            claim_name=claim_name,
            claim_uid=claim_uid,
            pod_name=pod_name,
            pod_uid=pod_uid,
            container_name=container_name,
            image=box.image,
            token_hash=runtime_token_hash(token),
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        runtime.observed_image_id = _validate_live_resources(runtime)
        runtime.full_clean()
        runtime.save()
        _audit(runtime, "ALLOW", "Provisioner certified exact live runtime resources.")
    return runtime, token


def authenticated_runtime(box_id, token):
    if not isinstance(token, str) or not re.fullmatch(r"alft_box_[A-Za-z0-9_-]{43}", token):
        return None
    try:
        UUID(str(box_id))
    except (ValueError, TypeError):
        return None
    return ManagedBoxRuntime.objects.filter(
        box__guid=box_id,
        box__deleted_at__isnull=True,
        box__organization__deleted_at__isnull=True,
        cluster__deleted_at__isnull=True,
        token_hash=runtime_token_hash(token),
        token_expires_at__gt=timezone.now(),
    ).first()


def _audit(runtime, decision, reason):
    AuditEvent.objects.create(
        organization_id=runtime.box.organization_id,
        actor_kind="system",
        actor_id=str(runtime.guid),
        action="agent_box.runtime.validate",
        decision=decision,
        target_kind="agent_box",
        target_id=str(runtime.box.guid),
        target_slug=runtime.box.slug,
        reasoning=[reason],
    )


def _require_manifest(manifest, kind, name, uid, namespace):
    if not isinstance(manifest, dict):
        raise RuntimeAuthorityError("The managed runtime resource is unavailable.")
    metadata = manifest.get("metadata")
    if (
        not isinstance(metadata, dict)
        or manifest.get("kind") != kind
        or metadata.get("name") != name
        or metadata.get("uid") != uid
        or metadata.get("namespace") != namespace
        or metadata.get("deletionTimestamp")
    ):
        raise RuntimeAuthorityError("The managed runtime resource identity changed.")


def _validate_live_resources(runtime):
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    cluster = runtime.cluster
    if (
        cluster.deleted_at is not None
        or not cluster.is_active
        or cluster.lifecycle != "managed"
        or cluster.organization_id not in (None, runtime.box.organization_id)
    ):
        raise RuntimeAuthorityError("The managed runtime cluster is unavailable.")
    driver = _driver_for_cluster(cluster)
    context = _context_for_cluster(cluster)
    pod = driver.get_manifest(context.slug, runtime.namespace, "Pod", runtime.pod_name)
    job = driver.get_manifest(context.slug, runtime.namespace, "Job", runtime.job_name)
    claim = driver.get_manifest(context.slug, runtime.namespace, "PersistentVolumeClaim", runtime.claim_name)
    _require_manifest(pod, "Pod", runtime.pod_name, runtime.pod_uid, runtime.namespace)
    _require_manifest(job, "Job", runtime.job_name, runtime.job_uid, runtime.namespace)
    _require_manifest(
        claim, "PersistentVolumeClaim", runtime.claim_name, runtime.claim_uid, runtime.namespace
    )
    if (
        pod["metadata"].get("labels", {}).get("astrolift.dev/agent-box") != str(runtime.box.guid)
        or job["metadata"].get("labels", {}).get("astrolift.dev/agent-box") != str(runtime.box.guid)
        or not any(
            owner.get("kind") == "Job"
            and owner.get("uid") == runtime.job_uid
            and owner.get("name") == runtime.job_name
            and owner.get("controller") is True
            for owner in pod["metadata"].get("ownerReferences", [])
        )
        or pod.get("status", {}).get("phase") != "Running"
        or claim.get("status", {}).get("phase") != "Bound"
    ):
        raise RuntimeAuthorityError("The managed runtime pod does not own its expected storage.")
    containers = pod.get("spec", {}).get("containers", [])
    selected = next((c for c in containers if c.get("name") == runtime.container_name), None)
    if not selected or selected.get("image") != runtime.image:
        raise RuntimeAuthorityError("The managed runtime container changed.")
    statuses = pod.get("status", {}).get("containerStatuses", [])
    primary = [c for c in statuses if c.get("name") == runtime.container_name]
    if (
        len(primary) != 1
        or not primary[0].get("state", {}).get("running")
        or primary[0].get("image") != runtime.image
    ):
        raise RuntimeAuthorityError("The managed runtime container image is not running.")
    image_id = primary[0].get("imageID", "")
    if not isinstance(image_id, str):
        raise RuntimeAuthorityError("The managed runtime running image identity is unavailable.")
    image_id = image_id.removeprefix("docker-pullable://")
    if not re.fullmatch(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}", image_id) or (
        runtime.pk is not None and runtime.observed_image_id != image_id
    ):
        raise RuntimeAuthorityError("The managed runtime running image identity changed.")
    claims = {
        volume.get("name")
        for volume in pod.get("spec", {}).get("volumes", [])
        if volume.get("persistentVolumeClaim", {}).get("claimName") == runtime.claim_name
        and not volume.get("persistentVolumeClaim", {}).get("readOnly", False)
    }
    mounts = selected.get("volumeMounts", [])
    for target, subpath in (("/state", "state"), (runtime.workspace_path, "workspace")):
        matching = [mount for mount in mounts if mount.get("mountPath") == target]
        if (
            len(matching) != 1
            or matching[0].get("name") not in claims
            or matching[0].get("readOnly", False)
            or matching[0].get("subPath") != subpath
            or matching[0].get("subPathExpr")
            or any(mount.get("mountPath", "").startswith(target + "/") for mount in mounts)
        ):
            raise RuntimeAuthorityError("The managed runtime state and workspace must be persistent.")
    return image_id


def validate_runtime_authority(runtime_id, token, request):
    if (
        not isinstance(request, dict)
        or set(request) != {"version", "ownerEpoch", "podUid", "runtimeInstanceId"}
        or type(request.get("version")) is not int
        or request["version"] != 1
        or not isinstance(request.get("runtimeInstanceId"), str)
        or not re.fullmatch(r"[a-f0-9]{32}", request["runtimeInstanceId"])
    ):
        raise RuntimeAuthorityError("Invalid managed runtime validation request.")
    denied = None
    with transaction.atomic():
        runtime = (
            ManagedBoxRuntime.objects.select_for_update()
            .filter(pk=runtime_id, token_hash=runtime_token_hash(token))
            .first()
        )
        if runtime is None:
            raise RuntimeAuthorityError("The managed runtime credential was revoked.")
        box = AgentBox.objects.select_for_update().filter(pk=runtime.box_id).first()
        runtime.box = box
        try:
            if (
                box is None
                or box.organization.deleted_at is not None
                or box.status not in (AgentBox.Status.PROVISIONING, AgentBox.Status.RUNNING)
                or runtime.token_expires_at <= timezone.now()
                or str(runtime.owner_epoch) != request["ownerEpoch"]
                or runtime.pod_uid != request["podUid"]
                or box.external_id != runtime.job_name
                or box.namespace != runtime.namespace
                or box.image != runtime.image
                or runtime.runtime_instance_id not in ("", request["runtimeInstanceId"])
            ):
                raise RuntimeAuthorityError("The managed runtime ownership or incarnation changed.")
            _validate_live_resources(runtime)
            if runtime.token_expires_at <= timezone.now():
                raise RuntimeAuthorityError("The managed runtime credential expired during validation.")
            if not runtime.runtime_instance_id:
                runtime.runtime_instance_id = request["runtimeInstanceId"]
                runtime.save(update_fields=["runtime_instance_id", "updated_at", "version"])
            _audit(runtime, "ALLOW", "Exact live pod, job, persistent claim and owner epoch validated.")
        except RuntimeAuthorityError as error:
            denied = error
            if box is not None:
                _audit(runtime, "DENY", str(error))
        if denied is None:
            result = {
                "version": 1,
                "owner": {
                    "apiUrl": runtime.api_url,
                    "organizationId": str(box.organization.guid),
                    "boxId": str(box.guid),
                    "ownerEpoch": str(runtime.owner_epoch),
                    "workspacePath": runtime.workspace_path,
                },
                "incarnation": {
                    "namespace": runtime.namespace,
                    "podName": runtime.pod_name,
                    "podUid": runtime.pod_uid,
                    "containerName": runtime.container_name,
                    "runtimeInstanceId": runtime.runtime_instance_id,
                },
            }
    if denied:
        raise denied
    return result
