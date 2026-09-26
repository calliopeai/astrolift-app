"""K8s Job spawn backend for agent task dispatch (#49).

Spawns agent workloads as batch/v1 Jobs in the target cluster.
One Job per AgentTask. Job name: ``agent-task-<complete_task_guid_hex>``.

The pod uses the Workload's image + the Brief env vars injected by
brief_injector.inject_brief_into_job_spec().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from astrolift_dispatch.spawners.base import ContainerSpawner, SpawnResult, TaskStatus

if TYPE_CHECKING:
    from astrolift_agents.models import AgentTask

logger = logging.getLogger(__name__)

# Container port the agent's raw RFB server (x11vnc) listens on when VNC
# is enabled (mirrors core.schema.vnc_ws.VNC_PORT, the relay's
# port-forward target). The -vnc image serves raw RFB here; there is no
# noVNC/websockify in the pod — the control-plane relay is the websockify.
VNC_PORT = 5900


class K8sJobSpawner(ContainerSpawner):
    """Spawn agent tasks as batch/v1 K8s Jobs."""

    def __init__(self, cluster, namespace: str) -> None:
        self._cluster = cluster
        self._namespace = namespace
        self._spawn_cleanup_refs: dict[str, list[dict]] = {}

    def recover(self, task: AgentTask) -> SpawnResult | None:
        """Adopt an already-created task Job without rotating its credential."""
        import hashlib

        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        job_name = f"agent-task-{str(task.guid).replace('-', '')}"
        driver = _driver_for_cluster(self._cluster)
        ctx = _context_for_cluster(self._cluster)
        manifest = driver.get_manifest(ctx.slug, self._namespace, "Job", job_name)
        if manifest is None:
            return None
        metadata = manifest.get("metadata", {})
        if metadata.get("labels", {}).get("astrolift.dev/task-id") != str(task.guid):
            return SpawnResult(external_id="", ok=False, error="Existing agent Job belongs to another task")
        if metadata.get("deletionTimestamp"):
            return SpawnResult(external_id=job_name, ok=False, error="Existing agent Job is being deleted")
        containers = manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        keys = [
            item.get("value", "")
            for container in containers
            for item in container.get("env", [])
            if item.get("name") == "ASTROLIFT_CLUSTER_KEY"
        ]
        if not any(
            key and hashlib.sha256(key.encode()).hexdigest() == task.callback_token_hash for key in keys
        ):
            return SpawnResult(
                external_id=job_name, ok=False, error="Existing agent Job has no matching callback credential"
            )
        return SpawnResult(external_id=job_name)

    def reserve_input_wait(self, task: AgentTask, seconds: int) -> None:
        from copy import deepcopy

        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        driver = _driver_for_cluster(self._cluster)
        ctx = _context_for_cluster(self._cluster)
        name = task.external_id or task.dispatch_target.get("planned_external_id")
        if not name:
            raise RuntimeError("Task has no verifiable Job identity")
        job = driver.get_manifest(ctx.slug, self._namespace, "Job", name)
        if not job:
            raise RuntimeError("Agent Job is missing")
        metadata = job.get("metadata") or {}
        if (
            (metadata.get("labels") or {}).get("astrolift.dev/task-id") != str(task.guid)
            or metadata.get("name") != name
            or metadata.get("namespace") != self._namespace
            or not metadata.get("uid")
            or not metadata.get("resourceVersion")
            or metadata.get("deletionTimestamp")
        ):
            raise RuntimeError("Agent Job identity cannot be verified")
        if any(
            condition.get("status") == "True" and condition.get("type") in {"Complete", "Failed"}
            for condition in (job.get("status") or {}).get("conditions", [])
        ):
            raise RuntimeError("Agent Job has already ended")
        deadline = max(1, int(task.timeout_seconds or 300)) + seconds
        if job.get("spec", {}).get("activeDeadlineSeconds") == deadline:
            return
        manifest = deepcopy(job)
        manifest.pop("status", None)
        manifest["metadata"].pop("managedFields", None)
        manifest["spec"]["activeDeadlineSeconds"] = deadline
        # Retain UID/resourceVersion and the entire current spec. A deletion or
        # concurrent mutation must conflict, never create a replacement Job or
        # strip fields owned by the platform's existing SSA field manager.
        result = driver.apply_manifests(ctx.slug, self._namespace, [manifest])
        if not result.ok:
            raise RuntimeError("Could not reserve the agent Job input-wait deadline")

    def spawn(self, task: AgentTask) -> SpawnResult:
        """Create a K8s Job for the given AgentTask."""
        from astrolift_dispatch.brief_injector import inject_brief_into_job_spec
        from astrolift_dispatch.model_gateway import redact
        from astrolift_dispatch.snapshot_injector import inject_snapshot_into_job_spec
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        workload = task.agent_definition
        if workload is None:
            return SpawnResult(external_id="", ok=False, error="task has no agent_definition")

        # Refuse before anything is created, rather than spawning a Job
        # that cannot run the command it was given (#1698).
        if not _resolve_base_image(workload, getattr(task, "environment_spec", None)):
            return SpawnResult(
                external_id="",
                ok=False,
                error=(
                    f"agent {workload.slug!r} has no runnable image: its environment spec pins "
                    f"no image tag, names no known runtime, and its primary container declares "
                    f"no image. Pin one with "
                    f"`astro agent env-spec upsert --image-tag <ref>`, set a runtime on the "
                    f"spec, or give the container an image in astrolift.toml."
                ),
            )

        # A manifest sync can turn on allow_install after the spec went non-root.
        requested_spec = getattr(task, "environment_spec", None)
        if getattr(requested_spec, "run_as_non_root", False) and getattr(
            requested_spec, "allow_install", False
        ):
            from astrolift_dispatch.pod_hardening import NON_ROOT_INSTALL_CONFLICT

            return SpawnResult(external_id="", ok=False, error=NON_ROOT_INSTALL_CONFLICT)

        # UUIDv7 prefixes contain only time; truncating them collides across parallel tasks.
        job_name = f"agent-task-{str(task.guid).replace('-', '')}"
        # Mark the in-process cleanup plan before any ancillary resolution.
        # A credential/backend failure before manifests are applied must not
        # fall through to a database lookup that can mask the original error
        # or delay deletion of the Job and per-task Secret.
        self._spawn_cleanup_refs[job_name] = []

        # Model gateway (#1851): a spec that asks for it runs only behind it,
        # so every missing piece refuses the run before anything is created.
        gateway = None
        if requested_spec is not None and getattr(requested_spec, "model_gateway", False):
            from astrolift_dispatch.model_gateway import ModelGatewayError, resolve_model_gateway

            try:
                gateway = resolve_model_gateway(
                    cluster=self._cluster, organization=task.organization, spec=requested_spec
                )
            except ModelGatewayError as exc:
                logger.warning("k8s_job_spawner: model gateway refused Job %s: %s", job_name, exc)
                return SpawnResult(external_id=job_name, ok=False, error=str(exc))

        # Managed model: when the spec's ``managed_model`` switch is on, the
        # pod uses the cluster's cloud-native model provider (AWS→Bedrock,
        # GCP→Vertex) via a minted workload-identity ServiceAccount instead
        # of an ANTHROPIC_API_KEY. Resolve the driver, mint / reuse the
        # identity, and compute the model env BEFORE rendering so the SA
        # name + model env land on the Job. A provider that doesn't support
        # managed model (or missing identity config) fails the spawn with
        # one actionable error rather than a silent skip that would leave
        # the pod crash-looping on the missing key.
        spec = getattr(task, "environment_spec", None)
        model_service_account = ""
        model_env: list[dict] | None = None
        model_sa_manifest: dict | None = None
        if spec is not None and getattr(spec, "managed_model", False):
            from astrolift_dispatch.agent_model import (
                ManagedModelError,
                resolve_managed_model_wiring,
            )

            try:
                wiring = resolve_managed_model_wiring(cluster=self._cluster, namespace=self._namespace)
            except ManagedModelError as exc:
                logger.warning("k8s_job_spawner: managed model wiring failed for Job %s: %s", job_name, exc)
                return SpawnResult(external_id=job_name, ok=False, error=str(exc))
            model_service_account = wiring.service_account
            model_env = wiring.env
            model_sa_manifest = wiring.service_account_manifest

        # Build a minimal Job manifest from the agent workload
        job_manifest = _render_agent_job(
            job_name=job_name,
            workload=workload,
            namespace=self._namespace,
            task=task,
            service_account=model_service_account,
            model_env=model_env,
        )

        # Inject Brief env vars
        job_manifest = inject_brief_into_job_spec(job_manifest, task)
        # Provision + inject the per-task VNC snapshot PUT URL (no-op for
        # non-VNC tasks and when no blob store is configured).
        job_manifest = inject_snapshot_into_job_spec(job_manifest, task)

        # Resolve the spec's secret_refs to live values from the install's
        # secret store (the SAME store setAgentSecretValue writes into, via
        # self._cluster) and materialize them as a per-task K8s Secret the
        # pod mounts via secretKeyRef (#1173). Preflight fails the spawn with
        # one readable error listing every missing/empty ref rather than
        # letting the pod crash-loop on an unresolvable reference.
        from astrolift_dispatch.agent_secrets import (
            AgentSecretResolutionError,
            resolve_task_secret_manifest,
            task_secret_name,
        )

        try:
            secret_manifest = resolve_task_secret_manifest(
                cluster=self._cluster,
                spec=getattr(task, "environment_spec", None),
                secret_name=task_secret_name(job_name),
                namespace=self._namespace,
                task_guid=str(task.guid),
                # An agent that belongs to an app inherits that app's
                # managed-service bindings through this Secret (#1700).
                workload=workload,
                # Behind the gateway, what it takes over is not even read.
                exclude=gateway.owned_env_names if gateway is not None else frozenset(),
            )
        except AgentSecretResolutionError as exc:
            logger.warning("k8s_job_spawner: secret preflight failed for Job %s: %s", job_name, exc)
            return SpawnResult(external_id=job_name, ok=False, error=str(exc))

        # Bundle keys are enumerated from the live backend during preflight.
        # Inject the resulting names after resolution so a new bundle key is
        # available immediately even if the cached key list was stale when the
        # base Job was rendered. The per-task Secret already contains the
        # direct-ref-over-bundle merged value for each name.
        if secret_manifest:
            _inject_resolved_secret_env(
                job_manifest,
                secret_name=task_secret_name(job_name),
                env_names=list((secret_manifest.get("stringData") or {}).keys()),
            )

        # Every source has written its env by now (spec, bindings, bundles,
        # the Brief's [environment]), so the check sees all of them and
        # nothing can shadow the wiring.
        if gateway is not None:
            from astrolift_dispatch.model_gateway import ModelGatewayError

            try:
                gateway.wire_container(
                    job_manifest["spec"]["template"]["spec"]["containers"][0],
                    secret_name=task_secret_name(job_name),
                )
            except ModelGatewayError as exc:
                logger.warning("k8s_job_spawner: model gateway refused Job %s: %s", job_name, exc)
                return SpawnResult(external_id=job_name, ok=False, error=str(exc))

        # Final env dedupe (authoritative). SSA rejects duplicate env names.
        # Keeping the last occurrence preserves operator/direct-secret
        # precedence over package defaults while every secret reference points
        # at the same merged per-task Secret.
        _dedupe_job_container_env(job_manifest)

        # Minted last, just before the apply, so a refusal above never leaves
        # a live key behind. The key goes into the per-task Secret only.
        gateway_key = None
        if gateway is not None:
            from astrolift_dispatch.model_gateway import (
                ModelGatewayError,
                key_covers,
                revoke_run_key,
                task_agent_id,
                task_key_ttl,
                with_gateway_key,
            )

            agent_id = task_agent_id(task)
            # Recorded before the mint, with the connection that mints it, so
            # every stop path revokes the key through that install even if
            # this process dies between the mint and the apply.
            task.model_gateway_agent_id = agent_id
            task.model_gateway_connection = gateway.connection
            task.save(
                update_fields=["model_gateway_agent_id", "model_gateway_connection", "updated_at", "version"]
            )
            try:
                gateway_key = gateway.mint(
                    agent_id=agent_id,
                    ttl_seconds=task_key_ttl(task),
                    name=f"{workload.slug} task {task.guid}",
                    deployment_id=workload.slug,
                )
            except ModelGatewayError as exc:
                logger.warning("k8s_job_spawner: no gateway key for Job %s: %s", job_name, exc)
                if exc.maybe_minted:
                    revoke_run_key(
                        connection_id=gateway.connection.pk, agent_id=agent_id, expect_missing=True
                    )
                return SpawnResult(external_id=job_name, ok=False, error=str(exc))
            timeout = max(1, int(getattr(task, "timeout_seconds", 300) or 300))
            if not key_covers(gateway_key.expires_at, timeout):
                revoke_run_key(connection_id=gateway.connection.pk, agent_id=agent_id)
                error = (
                    f"Zentinelle caps the run's gateway key at {gateway_key.expires_at.isoformat()}, before "
                    f"the task's {timeout}s timeout ends, so it was not started; shorten the timeout, or "
                    "raise ASTROLIFT_AGENT_KEY_MAX_LIFETIME_SECONDS in Zentinelle"
                )
                return SpawnResult(external_id=job_name, ok=False, error=error)
            secret_manifest = with_gateway_key(
                secret_manifest,
                gateway_key,
                secret_name=task_secret_name(job_name),
                namespace=self._namespace,
                owner_guid=str(task.guid),
            )

        try:
            driver = _driver_for_cluster(self._cluster)
            ctx = _context_for_cluster(self._cluster)
            # Ensure the per-org agent namespace exists before creating the
            # Job — unlike the app deploy path there's no separate
            # provision_namespace step, so a first-ever agent dispatch would
            # otherwise 404 on the Job POST (namespace not found). Idempotent.
            ensure_ns = getattr(driver, "ensure_namespace", None)
            if callable(ensure_ns):
                ensure_ns(
                    ctx.slug,
                    self._namespace,
                    {"astrolift.io/managed-by": "platform", "astrolift.io/component": "agents"},
                    {},
                )
            from astrolift_services.filesystem_bindings import (
                agent_volume_bindings,
                cleanup_binding_resources,
                inject_bindings_into_workloads,
                preflight_bindings,
                resolve_binding_secret_manifests,
            )

            volume_bindings = agent_volume_bindings(spec)
            storage_manifests: list[dict] = []
            volume_secret_manifests: list[dict] = []
            if volume_bindings:
                preflight_bindings(
                    volume_bindings,
                    cluster_driver=driver,
                    cluster_slug=ctx.slug,
                    namespace=self._namespace,
                )
                rendered = inject_bindings_into_workloads(
                    [job_manifest],
                    volume_bindings,
                    namespace=self._namespace,
                    consumer_key=job_name,
                )
                job_manifest = next(row for row in rendered if row.get("kind") == "Job")
                storage_manifests = [row for row in rendered if row.get("kind") != "Job"]
                if any(binding.secret_refs for binding in volume_bindings):
                    from astrolift_dispatch.agent_secrets import resolve_secrets_backend

                    volume_secret_manifests = resolve_binding_secret_manifests(
                        volume_bindings,
                        secrets_backend=resolve_secrets_backend(self._cluster),
                        namespace=self._namespace,
                        consumer_key=job_name,
                    )
                self._spawn_cleanup_refs[job_name] = cleanup_binding_resources(
                    volume_bindings,
                    namespace=self._namespace,
                    consumer_key=job_name,
                )
            # The identity and credential objects must exist before the PV/PVC
            # and Job that reference them. Static PVs precede their pre-bound
            # claims; the Job is always last.
            manifests = []
            if model_sa_manifest is not None:
                manifests.append(model_sa_manifest)
            if secret_manifest:
                manifests.append(secret_manifest)
            manifests.extend(volume_secret_manifests)
            manifests.extend(
                sorted(
                    storage_manifests,
                    key=lambda row: {"PersistentVolume": 0, "PersistentVolumeClaim": 1}.get(
                        str(row.get("kind")),
                        2,
                    ),
                ),
            )
            from astrolift_dispatch.agent_network_fence import agent_fence_manifests

            manifests.extend(agent_fence_manifests(self._cluster, self._namespace))
            manifests.append(job_manifest)
            result = driver.apply_manifests(ctx.slug, self._namespace, manifests)
            if not getattr(result, "ok", False):
                error = result.summary() if hasattr(result, "summary") else "apply failed"
                error = redact(str(error), gateway_key)
                logger.warning("k8s_job_spawner: apply failed for Job %s: %s", job_name, error)
                self._cleanup_failed_spawn(job_name)
                return SpawnResult(external_id=job_name, ok=False, error=error)
            logger.info("k8s_job_spawner: created Job %s for task %s", job_name, task.guid)
            return SpawnResult(external_id=job_name)
        except Exception as exc:
            if gateway_key is None:
                logger.exception("k8s_job_spawner: failed to create Job %s", job_name)
            else:
                # No traceback: its message could carry the Secret's key.
                logger.warning(
                    "k8s_job_spawner: failed to create Job %s: %s", job_name, redact(repr(exc), gateway_key)
                )
            self._cleanup_failed_spawn(job_name)
            return SpawnResult(external_id=job_name, ok=False, error=redact(str(exc), gateway_key))

    def _cleanup_failed_spawn(self, job_name: str) -> None:
        """Best-effort cleanup after a non-atomic multi-manifest apply.

        Provider drivers may create the per-task Secret before rejecting the
        Job. Never leave that plaintext-bearing Secret behind when spawn
        reports failure. The managed-model ServiceAccount is intentionally not
        deleted because it is shared across tasks.
        """
        try:
            self.stop(job_name)
        except Exception:  # noqa: BLE001 - preserve the original spawn error
            logger.warning(
                "k8s_job_spawner: cleanup after failed spawn did not complete for Job %s",
                job_name,
                exc_info=True,
            )

    def status(self, external_id: str) -> TaskStatus:
        """Poll the K8s Job status."""
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        try:
            driver = _driver_for_cluster(self._cluster)
            ctx = _context_for_cluster(self._cluster)
            ws = driver.get_workload_status(ctx.slug, self._namespace, "Job", external_id)
            conditions = ws.conditions or []

            succeeded = any(
                str(c.get("type")) == "Complete" and str(c.get("status")) == "True" for c in conditions
            )
            failed = any(
                str(c.get("type")) == "Failed" and str(c.get("status")) == "True" for c in conditions
            )

            return TaskStatus(
                running=not succeeded and not failed,
                succeeded=succeeded,
                failed=failed,
            )
        except Exception as exc:
            return TaskStatus(failed=True, error_message=str(exc))

    def stop(self, external_id: str, *, expected_task_guid: str | None = None) -> None:
        """Delete the K8s Job (and its pod) for a running task, plus the
        per-task secret Secret if one was materialized (#1173).

        The Secret is deleted by its deterministic name; for a task that
        declared no secret_refs it simply isn't found (delete_manifests
        records it under ``not_found`` — a harmless no-op)."""
        from astrolift_dispatch.agent_secrets import task_secret_name
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        refs = [
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {"name": external_id, "namespace": self._namespace},
            },
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": task_secret_name(external_id), "namespace": self._namespace},
            },
        ]
        spawn_refs = self._spawn_cleanup_refs.pop(external_id, None)
        if spawn_refs is not None:
            refs.extend(spawn_refs)
        try:
            driver = _driver_for_cluster(self._cluster)
            ctx = _context_for_cluster(self._cluster)
            if expected_task_guid is not None:
                from _sdk.cluster import ClusterDriver

                if (
                    type(driver).get_manifest is ClusterDriver.get_manifest
                    or type(driver).list_manifests is ClusterDriver.list_manifests
                ):
                    raise RuntimeError("Cluster driver cannot verify task resource ownership and deletion")
                job = driver.get_manifest(ctx.slug, self._namespace, "Job", external_id)
                if job is not None:
                    metadata = job.get("metadata") or {}
                    if (metadata.get("labels") or {}).get("astrolift.dev/task-id") != expected_task_guid:
                        raise RuntimeError("Job belongs to a different agent task")
                    if not metadata.get("uid"):
                        raise RuntimeError("Job has no verifiable Kubernetes identity")
                    refs[0]["metadata"].update(uid=metadata["uid"])
                else:
                    # Do not race a missing read with deletion of a newly
                    # created Job under the same name without a UID precondition.
                    refs = refs[1:]
            # The Kubernetes Job API can orphan dependents when no policy is
            # supplied. Stop must terminate the pod, including a tool that is
            # still running after the runner's callback credential is revoked.
            result = driver.delete_manifests(ctx.slug, self._namespace, refs, propagation_policy="Foreground")
            if result is not None and not getattr(result, "ok", True):
                detail = result.summary() if hasattr(result, "summary") else "delete failed"
                raise RuntimeError(str(detail))
            logger.info("k8s_job_spawner: deleted Job %s (+ secret)", external_id)
        except Exception:  # noqa: BLE001
            logger.exception("k8s_job_spawner: failed to delete Job %s", external_id)
            raise
        finally:
            # After the kill, never before it: revocation reads the database
            # and calls Zentinelle. A pod that survived a failed delete loses
            # its model access all the same.
            _revoke_task_gateway_key(external_id)

        if spawn_refs is None:
            try:
                # After a worker restart the in-memory plan is gone. Recover
                # ancillary storage only after the Job kill has reached the
                # cluster; a database outage must never delay runaway-agent
                # termination.
                from astrolift_services.filesystem_bindings import (
                    agent_volume_bindings,
                    cleanup_binding_resources,
                )

                task = _task_for_external_id(external_id)
                if task is not None:
                    storage_refs = cleanup_binding_resources(
                        agent_volume_bindings(task.environment_spec),
                        namespace=self._namespace,
                        consumer_key=external_id,
                    )
                    if storage_refs:
                        storage_result = driver.delete_manifests(
                            ctx.slug,
                            self._namespace,
                            storage_refs,
                        )
                        if storage_result is not None and not getattr(storage_result, "ok", True):
                            detail = (
                                storage_result.summary()
                                if hasattr(storage_result, "summary")
                                else "storage delete failed"
                            )
                            raise RuntimeError(str(detail))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "k8s_job_spawner: Job %s was deleted but filesystem cleanup failed",
                    external_id,
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Job {external_id} was deleted but filesystem cleanup failed: {exc}",
                ) from exc

    def confirm_stopped(self, external_id: str) -> bool:
        from _sdk.cluster import ClusterDriver

        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        driver = _driver_for_cluster(self._cluster)
        if (
            type(driver).get_manifest is ClusterDriver.get_manifest
            or type(driver).list_manifests is ClusterDriver.list_manifests
        ):
            raise RuntimeError("Cluster driver cannot confirm task resource deletion")
        ctx = _context_for_cluster(self._cluster)
        if driver.get_manifest(ctx.slug, self._namespace, "Job", external_id) is not None:
            return False
        pods = driver.list_manifests(ctx.slug, self._namespace, "Pod")
        if not isinstance(pods, list):
            raise RuntimeError("Cluster did not return a valid pod listing")
        for pod in pods:
            meta = pod.get("metadata") or {}
            labels = meta.get("labels") or {}
            if (
                labels.get("job-name") == external_id
                or labels.get("batch.kubernetes.io/job-name") == external_id
                or any(
                    ref.get("kind") == "Job" and ref.get("name") == external_id
                    for ref in meta.get("ownerReferences", [])
                )
            ):
                return False
        return True

    def cleanup_task_secret(self, external_id: str) -> None:
        """Delete only the plaintext-bearing per-task Secret.

        Completed Job/pod objects remain available to the log surface. The
        Secret is no longer needed once the process has received its env and
        must not linger for the lifetime of retained Job history.
        """
        from astrolift_dispatch.agent_secrets import task_secret_name
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        # The run is over, so its gateway key goes first.
        _revoke_task_gateway_key(external_id)
        refs = [
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": task_secret_name(external_id),
                    "namespace": self._namespace,
                },
            },
        ]
        driver = _driver_for_cluster(self._cluster)
        ctx = _context_for_cluster(self._cluster)
        result = driver.delete_manifests(ctx.slug, self._namespace, refs)
        if result is not None and not getattr(result, "ok", True):
            detail = result.summary() if hasattr(result, "summary") else "secret delete failed"
            raise RuntimeError(str(detail))
        logger.info("k8s_job_spawner: deleted per-task Secret for Job %s", external_id)
        from astrolift_services.filesystem_bindings import (
            agent_volume_bindings,
            cleanup_binding_resources,
        )

        task = _task_for_external_id(external_id)
        if task is not None:
            volume_secret_refs = cleanup_binding_resources(
                agent_volume_bindings(task.environment_spec),
                namespace=self._namespace,
                consumer_key=external_id,
                include_storage=False,
            )
            if volume_secret_refs:
                volume_result = driver.delete_manifests(
                    ctx.slug,
                    self._namespace,
                    volume_secret_refs,
                )
                if volume_result is not None and not getattr(volume_result, "ok", True):
                    detail = (
                        volume_result.summary()
                        if hasattr(volume_result, "summary")
                        else "volume secret delete failed"
                    )
                    raise RuntimeError(str(detail))


def _revoke_task_gateway_key(external_id: str) -> None:
    """Revoke the gateway key of the run behind ``external_id``, if it had one (#1851).

    Best effort and never raises: the key's expiry is the backstop.
    """
    try:
        task = _task_for_external_id(external_id)
    except Exception:  # noqa: BLE001 - a stop path must not fail on revocation
        logger.warning("k8s_job_spawner: could not look up task %s to revoke its gateway key", external_id)
        return
    if task is None or not task.model_gateway_agent_id:
        return
    from astrolift_dispatch.model_gateway import revoke_run_key

    revoke_run_key(connection_id=task.model_gateway_connection_id, agent_id=task.model_gateway_agent_id)


def _task_for_external_id(external_id: str):
    """Resolve new full-guid Job names and legacy persisted external ids."""

    from uuid import UUID

    from django.core.exceptions import ImproperlyConfigured

    from astrolift_agents.models import AgentTask

    query = AgentTask.objects.select_related("environment_spec").filter(deleted_at__isnull=True)
    try:
        task = query.filter(external_id=external_id).first()
    except ImproperlyConfigured:
        return None
    if task is not None:
        return task
    prefix = "agent-task-"
    encoded = external_id.removeprefix(prefix)
    if not external_id.startswith(prefix) or len(encoded) != 32:
        return None
    try:
        task_guid = UUID(hex=encoded)
    except ValueError:
        return None
    return query.filter(guid=task_guid).first()


def _vnc_image(image: str) -> str:
    """Return the ``-vnc`` variant of an image ref.

    Appends ``-vnc`` to the repository component while preserving the tag
    or digest, e.g.::

        docker.io/calliopeai/astrolift-agent-claude:latest  ->
        docker.io/calliopeai/astrolift-agent-claude-vnc:latest

        docker.io/calliopeai/agent@sha256:abcd  ->
        docker.io/calliopeai/agent-vnc@sha256:abcd

    A digest-pinned ref (``...@sha256:...``) must be split on ``@``: the
    digest itself contains a ``:`` so an rpartition on ``:`` would slice
    inside the digest and yield a corrupt, unpullable ref. Otherwise the
    tag separator is the last ``:`` that is not part of a registry
    ``host:port`` (it must come after the last ``/``). Idempotent: an
    already ``-vnc`` repo is returned unchanged.
    """
    # Digest-pinned ref: everything before "@" is the repo, the rest
    # (including its internal ":") is the digest and must stay intact.
    if "@" in image:
        repo, sep, digest = image.partition("@")
        if repo.endswith("-vnc"):
            return image
        return f"{repo}-vnc{sep}{digest}"

    repo, sep, tag = image.rpartition(":")
    # A ":" before the final "/" is a registry port, not a tag separator.
    if not sep or "/" not in repo or "/" in tag:
        repo, sep, tag = image, "", ""
    if repo.endswith("-vnc"):
        return image
    return f"{repo}-vnc{sep}{tag}"


def _resolve_base_image(workload, spec) -> str:
    """Resolve the base agent image, honouring the runtime catalog.

    Precedence (most specific wins):
      1. An explicit ``spec.image_tag`` — a pinned private/ECR ref.
      2. The spec's ``runtime`` short-name resolved through the public
         runtime catalog (``docker.io/calliopeai/astrolift-agent-<name>``).
      3. The workload's primary container image.

    Returns ``""`` when none of them resolve, and the caller refuses the
    dispatch (#1698). This used to fall back to ``gcr.io/distroless/base``
    -- an image with no interpreter -- so a workload whose command the
    operator supplied spawned a Job that died in seconds with
    ``exec: "python": executable file not found in $PATH``. The task was
    marked failed with nothing pointing at the missing image, and the
    logs the CLI could reach were empty because the container never
    started. A placeholder base image is never a useful default for a
    workload whose command comes from somewhere else.

    The ``-vnc`` watchable variant is applied by the caller on top of this
    base, never here.
    """
    if spec is not None and spec.image_tag:
        return spec.image_tag
    if spec is not None and getattr(spec, "runtime", ""):
        from astrolift_agents.runtime_catalog import (
            is_known_runtime,
            resolve_runtime_image,
        )

        if is_known_runtime(spec.runtime):
            return resolve_runtime_image(spec.runtime)
    primary_container = workload.containers.filter(is_primary=True).first() if workload else None
    if primary_container and primary_container.image_ref:
        return primary_container.image_ref
    return ""


def _dedupe_job_container_env(job_manifest: dict) -> None:
    """De-duplicate the primary container's env by name, keeping the LAST
    occurrence, in place.

    Server-side apply rejects a container whose ``env`` has duplicate ``name``
    keys (a 500 "duplicate entries for key" that fails the whole Job POST), and
    the env is assembled from overlapping sources (managed-model provider env,
    the spec's env_vars + secret_refs, the Brief-prepended manifest
    [environment] config, dispatch input). Keeping the last occurrence preserves
    the precedence the assembly order encodes: the Brief prepends its defaults,
    the spec's own env lands later, so the spec/operator value wins. No-op when
    there are no duplicates, so a task with a clean env list is unchanged.
    """
    try:
        containers = job_manifest["spec"]["template"]["spec"]["containers"]
    except (KeyError, TypeError, IndexError):
        return
    if not containers:
        return
    env = containers[0].get("env")
    if not env:
        return
    by_name: dict[str, dict] = {}
    for entry in env:
        name = entry.get("name")
        if name is not None:
            by_name[name] = entry
    containers[0]["env"] = list(by_name.values())


def _inject_resolved_secret_env(job_manifest: dict, *, secret_name: str, env_names: list[str]) -> None:
    """Append secretKeyRef entries for the live, fully merged secret keys."""
    try:
        container = job_manifest["spec"]["template"]["spec"]["containers"][0]
    except (KeyError, TypeError, IndexError):
        return
    from astrolift_dispatch.agent_secrets import secret_env_entries

    refs = [{"env_var": name, "uri": ""} for name in env_names]
    container.setdefault("env", []).extend(secret_env_entries(secret_name, refs))


def _image_pull_policy(image: str) -> str:
    """Always refresh mutable implicit/explicit ``latest`` references."""
    if "@" in image:
        return "IfNotPresent"
    last_component = image.rsplit("/", 1)[-1]
    if ":" not in last_component or last_component.rsplit(":", 1)[-1] == "latest":
        return "Always"
    return "IfNotPresent"


def _render_agent_job(
    *,
    job_name: str,
    workload,
    namespace: str,
    task,
    service_account: str = "",
    model_env: list[dict] | None = None,
) -> dict:
    """Build a minimal batch/v1 Job manifest for an agent workload.

    ``service_account`` sets ``serviceAccountName`` on the pod template
    (managed-model tasks run under the annotated model SA; omitted → the
    namespace ``default``, unchanged). ``model_env`` is the managed-model
    provider env, prepended BEFORE the spec's own env so an explicit spec
    override of the same name wins (k8s takes the later duplicate — the
    same ordering ``agent_container_env`` relies on for plain-before-secret).
    """
    from astrolift_dispatch.agent_secrets import agent_container_env, task_secret_name
    from astrolift_dispatch.pod_hardening import harden_agent_pod

    primary_container = workload.containers.filter(is_primary=True).first()
    port = primary_container.port if primary_container else 0

    spec = getattr(task, "environment_spec", None)
    image = _resolve_base_image(workload, spec)

    # The spec's non-secret env vars go on the pod as plain env; its
    # secret_refs become secretKeyRef entries pointing at the per-task
    # Secret the spawner materializes (see K8sJobSpawner.spawn / #1173).
    # Values never touch this manifest. Managed-model env goes first, then the
    # spec's own env so a spec key overrides the injected default. Duplicate
    # keys are collapsed by the authoritative dedupe in K8sJobSpawner.spawn,
    # which runs after the Brief also prepends env (SSA rejects duplicate env
    # keys, so the dedupe must see every source).
    container_env = list(model_env or []) + agent_container_env(
        spec,
        task_secret_name(job_name),
        # An agent that belongs to an app reads that app's managed-service
        # bindings; they sit lowest in precedence (#1700).
        workload=workload,
    )

    # VNC-capable runs swap to the -vnc image variant and expose the
    # raw RFB port (5900) so the ASGI relay can port-forward into it.
    vnc = bool(getattr(task, "vnc_enabled", False))
    if vnc:
        image = _vnc_image(image)

    ports = [{"containerPort": port}] if port else []
    if vnc:
        ports = [p for p in ports if p["containerPort"] != VNC_PORT]
        ports.append({"containerPort": VNC_PORT})

    # Honour the primary container's command/args overrides. Both are
    # JSONField(default=list); only set the corresponding Job keys when
    # non-empty so an unconfigured container preserves the image's own
    # ENTRYPOINT/CMD (omitting the keys is not the same as setting them
    # to []). ``command`` maps to the pod's ``command`` (ENTRYPOINT) and
    # ``args`` to ``args`` (CMD).
    command = list(primary_container.command or []) if primary_container else []
    args = list(primary_container.args or []) if primary_container else []

    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "astrolift.dev/workload-kind": "agent",
                "astrolift.dev/task-id": str(task.guid),
                # The platform log surface (list_pods) selects on
                # astrolift.dev/app; key it to the task guid so an operator can
                # read an agent task's pod logs (agentTaskLogs / workflow stage
                # logs) without a task-id-specific selector in every driver
                # (#1013). Discovery passes the task guid as the app_slug.
                "astrolift.dev/app": str(task.guid),
            },
        },
        "spec": {
            "backoffLimit": 0,  # No retries — AgentTask handles retry logic
            "completions": 1,
            # Kubernetes enforces the task deadline even if the Temporal
            # worker or control plane is unavailable. This is the final
            # backstop against a runaway agent pod.
            "activeDeadlineSeconds": max(1, int(getattr(task, "timeout_seconds", 300) or 300)),
            "template": {
                "metadata": {
                    "labels": {
                        "astrolift.dev/task-id": str(task.guid),
                        "astrolift.dev/workload-kind": "agent",
                        "astrolift.dev/app": str(task.guid),
                    }
                },
                "spec": {
                    "restartPolicy": "Never",
                    # Managed-model tasks run under the annotated model SA so
                    # the pod-identity webhook injects the cloud model
                    # credential; omitted otherwise (the namespace default).
                    **({"serviceAccountName": service_account} if service_account else {}),
                    "containers": [
                        {
                            "name": "agent",
                            "image": image,
                            # Literal :latest must always resolve at Job create;
                            # immutable/versioned tags retain K8s's normal
                            # IfNotPresent behavior for reproducible runs.
                            "imagePullPolicy": _image_pull_policy(image),
                            "env": container_env,
                            **({"command": command} if command else {}),
                            **({"args": args} if args else {}),
                            **({"ports": ports} if ports else {}),
                        }
                    ],
                },
            },
        },
    }
    harden_agent_pod(
        job["spec"]["template"]["spec"], non_root=bool(getattr(spec, "run_as_non_root", False)), spec=spec
    )
    return job
