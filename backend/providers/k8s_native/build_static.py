"""In-cluster static-asset build/sync Job (#1010 static-site topology).

Platform-build mode for a ``static_site`` workload: clone the app's source,
run the operator's ``static_build_command``, ``aws s3 sync`` the built
output dir to the app's private S3 origin bucket, then invalidate the
CloudFront distribution -- all in one one-shot Kubernetes Job.

Mirrors the #978 kaniko spawn/poll shape (render SA + Job, apply through
the cluster's ``ClusterDriver``, poll ``get_workload_status`` to
Complete/Failed with heartbeats) but uses a generic builder image
carrying git + a node toolchain + the aws CLI and a plain shell pipeline
rather than kaniko -- a static build produces files, not a container image.

The pod authenticates to S3 + CloudFront via its IRSA ServiceAccount (no
static keys mounted); the caller (the ``sync_static_assets`` activity)
mints + binds that role before dispatch. A freshly-minted IRSA role's
trust can take tens of seconds to propagate, so the build script
preflights ``aws sts get-caller-identity`` with retry before the first
real AWS call (the #978 ``--push-retry`` analog for this driver).
"""

from __future__ import annotations

import time
from typing import Any

from _sdk._telemetry import driver_op, maybe_heartbeat
from _sdk.build import BuildResult
from k8s_native.build_kaniko import _condition_message, _condition_true, _job_name

# Generic builder image -- carries git, a node toolchain, and the aws CLI in
# one container, so a clone -> build -> sync -> invalidate pipeline runs with
# no extra tooling. A single image keeps a static build reproducible; swap
# this constant for an air-gapped mirror or a different toolchain (the Job
# renderer takes an ``image`` override, no interface change).
STATIC_BUILDER_IMAGE = "public.ecr.aws/sam/build-nodejs20.x:latest"

# Same platform namespace the kaniko build + agent tasks run in -- always
# present by build time, and the control-plane ClusterRole already grants
# jobs + serviceaccounts there.
BUILD_NAMESPACE = "astrolift-system"

# Pod-level backstop: a fresh pod re-runs the whole web-identity chain. The
# in-script sts preflight handles the common IRSA-propagation case; this only
# matters if the entire pod's projected token needs a clean re-assume.
_BACKOFF_LIMIT = 2

# IRSA trust-propagation preflight: poll sts:GetCallerIdentity until the
# freshly-bound role resolves before the first real S3/CloudFront call.
_IRSA_PREFLIGHT_ATTEMPTS = 6
_IRSA_PREFLIGHT_SLEEP_SECONDS = 10

# Modest resource floor so the build pod schedules without starving the node;
# the limit gives a real ``npm run build`` headroom without being unbounded.
_BUILD_RESOURCES = {
    "requests": {"cpu": "250m", "memory": "512Mi"},
    "limits": {"memory": "4Gi"},
}


def parse_git_source(source_uri: str) -> tuple[str, str]:
    """Split a platform ``source_uri`` into ``(clone_url, ref)``.

    The activity hands us ``git+https://<host>/<path>#<ref>`` (or a bare
    ``git+https://<host>/<path>``). ``git clone`` wants a plain HTTPS URL,
    so the ``git+`` prefix is stripped and the ``#<ref>`` fragment (a commit
    sha or a ``refs/heads/<branch>`` ref) is returned separately for an
    explicit checkout. Empty ``ref`` -> clone the default branch.
    """
    body = source_uri.strip()
    if body.startswith("git+"):
        body = body[len("git+") :]
    url, _, ref = body.partition("#")
    return url.rstrip("/"), ref.strip()


def render_static_build_service_account(*, name: str, namespace: str, role_arn: str) -> dict[str, Any]:
    """ServiceAccount the static-build pod runs under.

    Annotated with the cloud's workload-identity role ARN so the pod-identity
    webhook injects S3 + CloudFront credentials. ``role_arn`` empty -> no
    annotation. Distinct ``astrolift.io/component=static-build`` label so the
    #995 orphan scan + operator filters can tell it apart from the kaniko
    build SA."""
    annotations = {"eks.amazonaws.com/role-arn": role_arn} if role_arn else {}
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "astrolift.io/managed-by": "platform",
                "astrolift.io/component": "static-build",
            },
            **({"annotations": annotations} if annotations else {}),
        },
    }


def _build_script(
    *,
    clone_url: str,
    ref: str,
    build_command: str,
    output_dir: str,
    bucket: str,
    distribution_id: str,
) -> str:
    """The one-shot shell pipeline the build container runs.

    Preflights IRSA trust propagation (#978), clones the source, runs the
    operator's build command, syncs the built output dir to the private
    origin bucket (``--delete`` mirrors removed files), then busts the
    CloudFront cache. ``set -euo pipefail`` fails the Job on any step so the
    activity's terminal RetryPolicy surfaces a real failure rather than a
    half-synced site."""
    lines = [
        "set -euo pipefail",
        # IRSA trust can lag the role mint by tens of seconds -- preflight
        # sts:GetCallerIdentity with retry before the first real AWS call.
        f"for i in $(seq 1 {_IRSA_PREFLIGHT_ATTEMPTS}); do "
        f"aws sts get-caller-identity && break; "
        f'echo "awaiting IRSA trust propagation ($i)"; '
        f"sleep {_IRSA_PREFLIGHT_SLEEP_SECONDS}; done",
        f"git clone {clone_url} /workspace",
        "cd /workspace",
    ]
    if ref:
        # Strip a refs/heads/ prefix so `git checkout` takes the bare branch
        # name; a commit sha checks out verbatim.
        checkout = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
        lines.append(f"git checkout {checkout}")
    lines.append(build_command)
    lines.append(f"aws s3 sync {output_dir} s3://{bucket} --delete")
    lines.append(f"aws cloudfront create-invalidation --distribution-id {distribution_id} --paths '/*'")
    return "\n".join(lines)


def render_static_build_job(
    *,
    job_name: str,
    namespace: str,
    service_account: str,
    source_context: str,
    build_command: str,
    output_dir: str,
    bucket: str,
    distribution_id: str,
    region: str,
    image: str = STATIC_BUILDER_IMAGE,
) -> dict[str, Any]:
    """Render the one-shot static-asset build Job.

    ``source_context`` is the platform ``git+https://...#ref`` source URI;
    it is split into a clone URL + ref for an explicit checkout. The single
    container runs the clone -> build -> sync -> invalidate pipeline under
    the IRSA ServiceAccount. ``AWS_REGION`` is set so the aws CLI targets the
    bucket's region. ``backoffLimit`` gives a pod-level backstop for the rare
    case the in-script sts preflight isn't enough."""
    clone_url, ref = parse_git_source(source_context)
    script = _build_script(
        clone_url=clone_url,
        ref=ref,
        build_command=build_command,
        output_dir=output_dir,
        bucket=bucket,
        distribution_id=distribution_id,
    )
    labels = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/component": "static-build",
        "astrolift.dev/app": job_name,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "namespace": namespace, "labels": labels},
        "spec": {
            "backoffLimit": _BACKOFF_LIMIT,
            "completions": 1,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": service_account,
                    "containers": [
                        {
                            "name": "static-build",
                            "image": image,
                            "command": ["sh", "-c", script],
                            "env": [{"name": "AWS_REGION", "value": region}],
                            "resources": _BUILD_RESOURCES,
                        }
                    ],
                },
            },
        },
    }


class StaticAssetBuildDriver:
    """Runs the static-asset build/sync as a Job on the target cluster.

    Constructor surface mirrors ``KanikoBuildDriver`` so the activity wiring
    is uniform; the build inputs (source, command, output dir, bucket,
    distribution) are passed to ``build`` since they vary per workload, not
    per driver instance."""

    def __init__(
        self,
        *,
        cluster_driver: Any,
        cluster_slug: str,
        service_account: str,
        service_account_role_arn: str = "",
        namespace: str = BUILD_NAMESPACE,
        build_id: str = "",
        image: str = STATIC_BUILDER_IMAGE,
        poll_interval_seconds: float = 10.0,
        timeout_seconds: float = 1800.0,
        sleep: Any = time.sleep,
        clock: Any = time.monotonic,
    ) -> None:
        self._cluster_driver = cluster_driver
        self._cluster_slug = cluster_slug
        self._service_account = service_account
        self._sa_role_arn = service_account_role_arn
        self._namespace = namespace
        self._build_id = build_id
        self._image = image
        self._poll = poll_interval_seconds
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._clock = clock

    @driver_op(cloud="k8s_native", driver="static_build", audit=True, sensitive_kind="build.run")
    def build(
        self,
        *,
        source_uri: str,
        build_command: str,
        output_dir: str,
        bucket: str,
        distribution_id: str,
        region: str,
    ) -> BuildResult:
        job_name = _job_name(self._build_id or f"static-{bucket}")
        image_uri = f"s3://{bucket}"  # the "artifact" of a static build is the synced bucket
        manifests = [
            render_static_build_service_account(
                name=self._service_account,
                namespace=self._namespace,
                role_arn=self._sa_role_arn,
            ),
            render_static_build_job(
                job_name=job_name,
                namespace=self._namespace,
                service_account=self._service_account,
                source_context=source_uri,
                build_command=build_command,
                output_dir=output_dir,
                bucket=bucket,
                distribution_id=distribution_id,
                region=region,
                image=self._image,
            ),
        ]

        started = self._clock()
        apply_result = self._cluster_driver.apply_manifests(self._cluster_slug, self._namespace, manifests)
        if not getattr(apply_result, "ok", False):
            errors = apply_result.summary() if hasattr(apply_result, "summary") else ["apply failed"]
            return BuildResult(
                success=False,
                image_uri=image_uri,
                digest="",
                duration_seconds=self._clock() - started,
                errors=[str(e) for e in errors],
            )

        outcome = self._poll_to_completion(job_name, started)
        return BuildResult(
            success=outcome["success"],
            image_uri=image_uri,
            digest="",
            duration_seconds=self._clock() - started,
            errors=outcome["errors"],
        )

    def cancel(self, build_id: str) -> None:
        job_ref = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": _job_name(build_id), "namespace": self._namespace},
        }
        self._cluster_driver.delete_manifests(self._cluster_slug, self._namespace, [job_ref])

    def _poll_to_completion(self, job_name: str, started: float) -> dict[str, Any]:
        """Block until the build Job reaches a terminal state or times out.

        Reads the Job's ``Complete`` / ``Failed`` conditions (the same shape
        ``K8sJobSpawner.status`` + the kaniko driver key on). Heartbeats each
        tick so a long cold build doesn't trip the activity heartbeat
        timeout."""
        while True:
            maybe_heartbeat(f"static-build {job_name}")
            try:
                status = self._cluster_driver.get_workload_status(self._cluster_slug, self._namespace, "Job", job_name)
                conditions = status.conditions or []
            except Exception as exc:
                conditions = []
                if self._clock() - started < self._timeout:
                    self._sleep(self._poll)
                    continue
                return {"success": False, "errors": [f"status read failed: {exc}"]}

            if _condition_true(conditions, "Complete"):
                return {"success": True, "errors": []}
            if _condition_true(conditions, "Failed"):
                reason = _condition_message(conditions, "Failed") or "static build failed"
                return {"success": False, "errors": [reason]}

            if self._clock() - started >= self._timeout:
                return {"success": False, "errors": [f"static build timed out after {self._timeout:.0f}s"]}
            self._sleep(self._poll)
