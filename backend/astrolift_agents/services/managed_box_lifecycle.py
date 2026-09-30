import json
import re
import secrets
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from astrolift_agents.models import AgentBox, ManagedBoxRuntime
from astrolift_agents.services.managed_runtime_authority import (
    RuntimeAuthorityError,
    _audit,
    _validate_live_resources,
    certify_managed_runtime,
    configured_runtime_api_url,
    runtime_token_hash,
)

MANAGED_IDE_RUNTIME = "calliope-managed-ide"
EPOCH_LABEL = "astrolift.dev/managed-owner-epoch"


class ManagedBoxOwnershipConflict(RuntimeAuthorityError):
    pass


@contextmanager
def managed_start_guard(runtime, owner_box):
    if runtime is None:
        yield
        return
    entered = False
    try:
        with transaction.atomic():
            current = ManagedBoxRuntime.objects.select_for_update().get(pk=runtime.pk)
            box = AgentBox.objects.select_for_update().get(pk=current.box_id)
            if (
                current.phase != ManagedBoxRuntime.Phase.PENDING
                or not box.is_live
                or box.image != current.image
                or box.external_id != current.job_name
            ):
                from astrolift_agents.services.agent_box import AgentBoxError

                raise AgentBoxError("Managed box ownership changed before apply.")
            entered = True
            yield
    except Exception:
        if entered:
            from astrolift_agents.services.agent_box import _revoke_box_gateway_key

            _revoke_box_gateway_key(owner_box)
            revoked = revoke_managed_runtime(runtime.box)
            message = "Managed runtime start failed; persistent state is retained."
            try:
                delete_managed_runtime_objects(revoked)
            except Exception:
                message += " Cluster cleanup is incomplete; retry Stop."
            AgentBox.objects.filter(pk=runtime.box_id, status__in=AgentBox.LIVE_STATUSES).update(
                status=AgentBox.Status.FAILED,
                last_error=message,
                ended_at=timezone.now(),
                updated_at=timezone.now(),
                version=F("version") + 1,
            )
        raise


def is_managed_ide_box(box):
    return getattr(box.environment_spec, "runtime", "") == MANAGED_IDE_RUNTIME


def authority_secret_name(job_name):
    return f"{job_name}-authority"


def _driver(runtime):
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    return _driver_for_cluster(runtime.cluster), _context_for_cluster(runtime.cluster).slug


def _labels(runtime):
    return {"astrolift.dev/agent-box": str(runtime.box.guid), EPOCH_LABEL: str(runtime.owner_epoch)}


def stamp_runtime_owner(manifest, runtime):
    manifest.setdefault("metadata", {}).setdefault("labels", {}).update(_labels(runtime))
    if manifest.get("kind") == "Job":
        manifest["spec"]["template"]["metadata"].setdefault("labels", {}).update(_labels(runtime))
    return manifest


def _authority_secret(runtime, token=None):
    value = {"version": 1, "status": "pending"}
    if token is not None:
        value = {
            "version": 1,
            "apiUrl": runtime.api_url,
            "boxId": str(runtime.box.guid),
            "ownerEpoch": str(runtime.owner_epoch),
            "podUid": runtime.pod_uid,
            "token": token,
        }
    return stamp_runtime_owner(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {"name": authority_secret_name(runtime.job_name), "namespace": runtime.namespace},
            "stringData": {"authority.json": json.dumps(value, separators=(",", ":"))},
        },
        runtime,
    )


def prepare_managed_box(*, box, cluster, image, namespace, job_name, expected_version, expected_status):
    from astrolift_agents.services.agent_box import box_secret_name
    from astrolift_agents.services.managed_box_manifest import render_managed_box_pvc
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    spec = box.environment_spec
    if box.idle_timeout_seconds != 0:
        raise RuntimeAuthorityError(
            "Managed IDE boxes require explicit idleTimeoutSeconds: 0 and operator Stop."
        )
    if (
        not spec
        or spec.allow_install
        or spec.box_workspace
        or not spec.image_tag
        or not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", image)
    ):
        raise RuntimeAuthorityError("Managed IDE boxes require an immutable image and no boot-time setup.")
    browser_image = settings.MANAGED_IDE_BROWSER_IMAGE
    if not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", browser_image):
        raise RuntimeAuthorityError("Configure an immutable MANAGED_IDE_BROWSER_IMAGE first.")
    if not settings.MANAGED_IDE_STORAGE_CLASS or not settings.MANAGED_IDE_CSI_DRIVER:
        raise RuntimeAuthorityError("Configure managed IDE storage class and CSI driver first.")
    if cluster.organization_id not in (None, box.organization_id) or not cluster.is_active:
        raise RuntimeAuthorityError("The managed IDE cluster is unavailable.")
    driver = _driver_for_cluster(cluster)
    cluster_slug = _context_for_cluster(cluster).slug
    classes = {row.name: row for row in driver.list_storage_classes(cluster_slug)}
    selected = classes.get(settings.MANAGED_IDE_STORAGE_CLASS)
    if (
        selected is None
        or selected.provisioner != settings.MANAGED_IDE_CSI_DRIVER
        or selected.reclaim_policy != "Retain"
        or selected.provisioner not in driver.list_csi_drivers(cluster_slug)
    ):
        raise RuntimeAuthorityError(
            "Managed IDE storage requires an installed CSI driver and Retain storage class."
        )
    claim_name = f"{job_name}-state"
    claim = render_managed_box_pvc(
        box=box,
        namespace=namespace,
        claim_name=claim_name,
        storage_class_name=settings.MANAGED_IDE_STORAGE_CLASS,
        capacity=settings.MANAGED_IDE_STORAGE_CAPACITY,
    )
    with transaction.atomic():
        current = AgentBox.objects.select_for_update().get(pk=box.pk)
        if current.version != expected_version or current.status != expected_status:
            raise ManagedBoxOwnershipConflict("Managed box start was cancelled or superseded.")
        if ManagedBoxRuntime.all_objects.filter(box=current).exists():
            raise ManagedBoxOwnershipConflict(
                "This managed box already owns recovery state; automatic replacement is refused."
            )
        for kind, name in (
            ("Job", job_name),
            ("PersistentVolumeClaim", claim_name),
            ("Secret", authority_secret_name(job_name)),
            ("Secret", box_secret_name(job_name)),
        ):
            if driver.get_manifest(cluster_slug, namespace, kind, name) is not None:
                raise RuntimeAuthorityError("A managed runtime resource name is already in use.")
        runtime = ManagedBoxRuntime(
            box=current,
            cluster=cluster,
            phase=ManagedBoxRuntime.Phase.PENDING,
            api_url=configured_runtime_api_url(),
            workspace_path="/workspace",
            namespace=namespace,
            job_name=job_name,
            claim_name=claim_name,
            container_name="runtime",
            image=image,
            token_expires_at=timezone.now(),
        )
        runtime.full_clean()
        runtime.save()
        current.status = AgentBox.Status.PROVISIONING
        current.image, current.external_id, current.namespace = image, job_name, namespace
        current.save(update_fields=["status", "image", "external_id", "namespace", "updated_at", "version"])
    return runtime, [stamp_runtime_owner(claim, runtime), _authority_secret(runtime)]


def _owned_resource(runtime, kind, name, expected_uid=""):
    driver, cluster_slug = _driver(runtime)
    manifest = driver.get_manifest(cluster_slug, runtime.namespace, kind, name)
    if manifest is None:
        return None
    meta = manifest.get("metadata", {})
    if (
        manifest.get("kind") != kind
        or meta.get("name") != name
        or meta.get("namespace") != runtime.namespace
        or not meta.get("uid")
        or any(meta.get("labels", {}).get(k) != v for k, v in _labels(runtime).items())
        or (expected_uid and meta["uid"] != expected_uid)
    ):
        raise RuntimeAuthorityError("Managed runtime resource ownership changed.")
    return manifest


def record_managed_resource_ids(runtime_id):
    with transaction.atomic():
        runtime = ManagedBoxRuntime.objects.select_for_update().get(pk=runtime_id)
        if runtime.phase != ManagedBoxRuntime.Phase.PENDING:
            raise RuntimeAuthorityError("The managed runtime reservation is no longer pending.")
        job = _owned_resource(runtime, "Job", runtime.job_name, runtime.job_uid)
        claim = _owned_resource(runtime, "PersistentVolumeClaim", runtime.claim_name, runtime.claim_uid)
        if job is None or claim is None:
            raise RuntimeAuthorityError("Managed runtime resources have not been applied.")
        runtime.job_uid, runtime.claim_uid = job["metadata"]["uid"], claim["metadata"]["uid"]
        runtime.save(update_fields=["job_uid", "claim_uid", "updated_at", "version"])


def reconcile_managed_runtime(runtime_id):
    from core.cluster_observability import list_app_pods

    with transaction.atomic():
        runtime = ManagedBoxRuntime.objects.select_for_update().get(pk=runtime_id)
        box = AgentBox.objects.select_for_update().get(pk=runtime.box_id)
        runtime.box = box
        if runtime.phase == ManagedBoxRuntime.Phase.REVOKED or not box.is_live:
            return False
        if runtime.phase == ManagedBoxRuntime.Phase.PENDING:
            if not runtime.job_uid or not runtime.claim_uid:
                record_managed_resource_ids(runtime.pk)
                runtime.refresh_from_db()
            pods = list_app_pods(cluster=runtime.cluster, namespace=runtime.namespace, app_slug=str(box.guid))
            ready = [pod for pod in pods if getattr(pod, "ready", False)]
            if len(ready) != 1:
                return False
            pod = _owned_resource(runtime, "Pod", ready[0].name)
            if pod is None or pod["metadata"].get("deletionTimestamp"):
                return False
            runtime, token = certify_managed_runtime(
                box_id=box.pk,
                cluster=runtime.cluster,
                workspace_path=runtime.workspace_path,
                job_uid=runtime.job_uid,
                claim_name=runtime.claim_name,
                claim_uid=runtime.claim_uid,
                pod_name=ready[0].name,
                pod_uid=pod["metadata"]["uid"],
                container_name=runtime.container_name,
                runtime_id=runtime.pk,
            )
        else:
            _validate_live_resources(runtime)
            if runtime.token_expires_at > timezone.now() + timedelta(minutes=15):
                return True
            token = "alft_box_" + secrets.token_urlsafe(32)
            runtime.token_hash = runtime_token_hash(token)
            runtime.token_expires_at = timezone.now() + timedelta(hours=1)
            runtime.save(update_fields=["token_hash", "token_expires_at", "updated_at", "version"])
        previous_secret = _owned_resource(runtime, "Secret", authority_secret_name(runtime.job_name))
        if previous_secret is None:
            raise RuntimeAuthorityError("The managed runtime authority Secret is missing.")
        manifest = _authority_secret(runtime, token)
        manifest["metadata"]["uid"] = previous_secret["metadata"]["uid"]
        manifest["metadata"]["resourceVersion"] = previous_secret["metadata"]["resourceVersion"]
        driver, cluster_slug = _driver(runtime)
        try:
            result = driver.apply_manifests(cluster_slug, runtime.namespace, [manifest])
        except Exception:
            raise RuntimeAuthorityError("Managed runtime credential delivery failed.") from None
        if not getattr(result, "ok", False):
            raise RuntimeAuthorityError("Managed runtime credential delivery failed.")
        _audit(runtime, "ALLOW", "Runtime credential delivered to its certified pod Secret.")
        box.pod_name = runtime.pod_name
        box.save(update_fields=["pod_name", "updated_at", "version"])
    return True


def revoke_managed_runtime(box):
    with transaction.atomic():
        runtime = ManagedBoxRuntime.objects.select_for_update().filter(box_id=box.pk).first()
        if runtime is None:
            return None
        runtime.phase = ManagedBoxRuntime.Phase.REVOKED
        runtime.token_hash = ""
        runtime.token_expires_at = timezone.now()
        runtime.save(update_fields=["phase", "token_hash", "token_expires_at", "updated_at", "version"])
    return runtime


def cancel_managed_box_start(box):
    with transaction.atomic():
        current = AgentBox.objects.select_for_update().filter(pk=box.pk).first()
        if (
            current is not None
            and current.is_live
            and (
                current.status in (AgentBox.Status.PENDING, AgentBox.Status.PROVISIONING)
                or is_managed_ide_box(current)
                or ManagedBoxRuntime.objects.filter(box_id=box.pk).exists()
            )
        ):
            current.status = AgentBox.Status.STOPPED
            current.save(update_fields=["status", "updated_at", "version"])


def delete_managed_runtime_objects(runtime):
    from astrolift_agents.services.agent_box import box_secret_name

    refs = []
    refused = False
    for kind, name, uid in (
        ("Job", runtime.job_name, runtime.job_uid),
        ("Secret", authority_secret_name(runtime.job_name), ""),
        ("Secret", box_secret_name(runtime.job_name), ""),
    ):
        try:
            manifest = _owned_resource(runtime, kind, name, uid)
        except RuntimeAuthorityError:
            refused = True
            continue
        if manifest is not None:
            refs.append(
                {
                    "apiVersion": manifest["apiVersion"],
                    "kind": kind,
                    "metadata": {
                        key: manifest["metadata"][key]
                        for key in ("name", "namespace", "uid", "resourceVersion")
                        if key in manifest["metadata"]
                    },
                }
            )
    driver, cluster_slug = _driver(runtime)
    result = driver.delete_manifests(cluster_slug, runtime.namespace, refs, propagation_policy="Foreground")
    if result is not None and not getattr(result, "ok", True):
        raise RuntimeAuthorityError(
            "Managed runtime teardown did not complete; persistent state is retained."
        )
    if refused:
        raise RuntimeAuthorityError(
            "Managed runtime resource ownership changed; foreign objects were retained."
        )
