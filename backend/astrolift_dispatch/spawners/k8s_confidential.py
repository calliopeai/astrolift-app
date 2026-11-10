"""Direct, policy-bound Pods for confidential tasks; ordinary agents keep their Jobs."""

from __future__ import annotations

from copy import deepcopy

from astrolift_dispatch.spawners.base import SpawnResult, TaskStatus
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner, _task_for_external_id


def confidential_config(cluster):
    from astrolift_clusters.nitro import confidential_config as configured

    return configured(cluster)


class ConfidentialPodSpawner(K8sJobSpawner):
    confidential_pod = True

    def spawn(self, task):
        from astrolift_dispatch.pod_hardening import AgentRuntimeClassError
        from astrolift_services.filesystem_bindings import agent_volume_bindings
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        spec = getattr(task, "environment_spec", None)
        if (
            spec is None
            or not getattr(spec, "run_as_non_root", False)
            or getattr(spec, "allow_install", False)
            or getattr(spec, "managed_model", False)
            or getattr(spec, "model_gateway", False)
            or getattr(task, "vnc_enabled", False)
            or agent_volume_bindings(spec)
        ):
            return SpawnResult(
                "",
                False,
                "Confidential tasks require non-root baked images without boot installs, "
                "managed models, gateway, VNC or filesystem bindings",
            )
        try:
            from astrolift_clusters.nitro import require_task_mode

            require_task_mode(self._cluster, "confidential", self._namespace)
            self._config = confidential_config(self._cluster)
            driver = _driver_for_cluster(self._cluster)
            context = _context_for_cluster(self._cluster)
            validate = getattr(driver, "validate_confidential_task", None)
            if not callable(validate):
                raise ValueError("Confidential task images and attestation require the AWS EKS provider")
            validate(context.slug, self._namespace, self._config)
            old = (task.dispatch_target or {}).get("nitro_confidential")
            if old:
                if driver.get_manifest(context.slug, self._namespace, "Pod", self._name(task)) is not None:
                    raise ValueError(
                        "Existing confidential Pod must be recovered before preparing another image"
                    )
                driver.cleanup_confidential_task(old)
        except AgentRuntimeClassError as exc:
            return SpawnResult("", False, str(exc))
        except Exception:
            return SpawnResult(
                "",
                False,
                "Confidential task prerequisites or previous-run cleanup failed; "
                "verify signed tooling, registry and Pod Identity configuration",
            )
        return super().spawn(task)

    @staticmethod
    def _name(task):
        return "agent-task-" + str(task.guid).replace("-", "")

    def _apply_task_manifests(self, driver, ctx, task, manifests, secret_manifest):
        job = manifests[-1]
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": deepcopy(job["metadata"]),
            "spec": deepcopy(job["spec"]["template"]["spec"]),
        }
        pod["spec"]["activeDeadlineSeconds"] = job["spec"]["activeDeadlineSeconds"]

        def save(checkpoint):
            task.dispatch_target = {
                **(task.dispatch_target or {}),
                "nitro_confidential": deepcopy(checkpoint),
            }
            task.save(update_fields=["dispatch_target", "updated_at", "version"])

        final, _ = driver.prepare_confidential_task(
            ctx.slug,
            self._namespace,
            config=self._config,
            pod=pod,
            secret=secret_manifest,
            task_id=str(task.guid).replace("-", ""),
            callback_hash=task.callback_token_hash,
            save=save,
        )
        task.refresh_from_db(fields=["status", "cancel_requested_at"])
        if task.cancel_requested_at or task.status in task.TERMINAL_STATUSES:
            raise RuntimeError("Task ended or was cancelled during confidential image preparation")
        prefix = [row for row in manifests[:-1] if row.get("kind") != "Secret"]
        if prefix:
            applied = driver.apply_manifests(ctx.slug, self._namespace, prefix)
            if not applied.ok:
                return applied
        return driver.apply_manifests(ctx.slug, self._namespace, [final], create_only=True)

    def recover(self, task):
        from aws.nitro_confidential import POLICY

        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        driver = _driver_for_cluster(self._cluster)
        pod = driver.get_manifest(
            _context_for_cluster(self._cluster).slug, self._namespace, "Pod", self._name(task)
        )
        if pod is None:
            return None
        if (
            not isinstance(pod, dict)
            or not isinstance(pod.get("metadata"), dict)
            or not isinstance(pod.get("spec"), dict)
        ):
            return SpawnResult(
                self._name(task), False, "Existing confidential Pod has malformed metadata or spec"
            )
        metadata, spec = pod.get("metadata") or {}, pod.get("spec") or {}
        checkpoint = (task.dispatch_target or {}).get("nitro_confidential") or {}
        if (
            not isinstance(checkpoint, dict)
            or not isinstance(metadata.get("labels", {}), dict)
            or not isinstance(metadata.get("annotations", {}), dict)
        ):
            return SpawnResult(
                self._name(task), False, "Existing confidential Pod has malformed run authority"
            )
        containers = spec.get("containers")
        if not isinstance(containers, list) or any(not isinstance(row, dict) for row in containers):
            return SpawnResult(self._name(task), False, "Existing confidential Pod has malformed containers")
        if (
            metadata.get("deletionTimestamp")
            or not metadata.get("uid")
            or (metadata.get("labels") or {}).get("astrolift.dev/task-id") != str(task.guid)
            or checkpoint.get("task_id") != str(task.guid).replace("-", "")
            or checkpoint.get("callback_hash") != task.callback_token_hash
            or not checkpoint.get("image")
            or not checkpoint.get("policy")
            or spec.get("runtimeClassName") != "confidential-sandbox"
            or len(containers) != 1
            or containers[0].get("image") != checkpoint["image"]
            or (metadata.get("annotations") or {}).get(POLICY) != checkpoint["policy"]
        ):
            return SpawnResult(
                self._name(task),
                False,
                "Existing confidential Pod does not match the run image/policy identity",
            )
        # Compare the prepared spec through its complete hash, ignoring only server identity/status.
        from aws.nitro_confidential import prepared_pod_hash

        if prepared_pod_hash(pod) != checkpoint.get("pod_sha256"):
            return SpawnResult(self._name(task), False, "Confidential Pod changed after policy preparation")
        return SpawnResult(self._name(task))

    def reserve_input_wait(self, task, seconds):
        raise RuntimeError("Confidential Pod deadlines cannot be extended for interactive input waits")

    def status(self, external_id):
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        pod = _driver_for_cluster(self._cluster).get_manifest(
            _context_for_cluster(self._cluster).slug,
            self._namespace,
            "Pod",
            external_id,
        )
        if pod is None:
            return TaskStatus(failed=True, error_message="Confidential task Pod is missing")
        status = pod.get("status") or {}
        phase = status.get("phase")
        terminated = [
            row["state"]["terminated"]
            for row in status.get("containerStatuses", [])
            if (row.get("state") or {}).get("terminated")
        ]
        code = terminated[0].get("exitCode") if terminated else None
        return TaskStatus(
            running=phase == "Running",
            succeeded=phase == "Succeeded",
            failed=phase == "Failed",
            exit_code=code,
            error_message="Confidential task failed" if phase == "Failed" else "",
        )

    def stop(self, external_id, *, expected_task_guid=None):
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        driver = _driver_for_cluster(self._cluster)
        ctx = _context_for_cluster(self._cluster)
        pod = driver.get_manifest(ctx.slug, self._namespace, "Pod", external_id)
        if pod is not None:
            from aws.nitro_confidential import POLICY

            task = _task_for_external_id(external_id)
            checkpoint = (task.dispatch_target or {}).get("nitro_confidential") if task else None
            metadata = pod.get("metadata") or {}
            guid = (metadata.get("labels") or {}).get("astrolift.dev/task-id", "")
            if (
                not guid
                or "agent-task-" + guid.replace("-", "") != external_id
                or not metadata.get("uid")
                or (expected_task_guid is not None and guid != str(expected_task_guid))
            ):
                raise RuntimeError("Confidential Pod ownership is not verifiable; refusing deletion")
            containers = (pod.get("spec") or {}).get("containers") or []
            if (
                not checkpoint
                or len(containers) != 1
                or not isinstance(containers[0], dict)
                or containers[0].get("image") != checkpoint.get("image")
                or not checkpoint.get("policy")
                or (metadata.get("annotations") or {}).get(POLICY) != checkpoint["policy"]
            ):
                raise RuntimeError("Confidential Pod image/policy ownership differs; refusing deletion")
            result = driver.delete_manifests(
                ctx.slug,
                self._namespace,
                [
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {
                            "name": external_id,
                            "namespace": self._namespace,
                            "uid": metadata["uid"],
                        },
                    }
                ],
                propagation_policy="Foreground",
            )
            if result is not None and not result.ok:
                raise RuntimeError("Confidential task Pod deletion failed")
        if self.confirm_stopped(external_id):
            self._cleanup_run(external_id)

    def confirm_stopped(self, external_id):
        from core.cluster_management import _context_for_cluster, _driver_for_cluster

        absent = (
            _driver_for_cluster(self._cluster).get_manifest(
                _context_for_cluster(self._cluster).slug,
                self._namespace,
                "Pod",
                external_id,
            )
            is None
        )
        if absent:
            self._cleanup_run(external_id)
        return absent

    def _cleanup_run(self, external_id):
        from core.cluster_management import _driver_for_cluster

        task = _task_for_external_id(external_id)
        checkpoint = (task.dispatch_target or {}).get("nitro_confidential") if task else None
        if checkpoint and not checkpoint.get("cleanup_complete"):
            _driver_for_cluster(self._cluster).cleanup_confidential_task(checkpoint)
            task.dispatch_target = {
                **task.dispatch_target,
                "nitro_confidential": {**checkpoint, "cleanup_complete": True},
            }
            task.save(update_fields=["dispatch_target", "updated_at", "version"])

    def cleanup_task_secret(self, external_id):
        task = _task_for_external_id(external_id)
        if task is not None and task.status in task.TERMINAL_STATUSES:
            self._cleanup_run(external_id)
