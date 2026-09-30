"""In-cluster kaniko BuildDriver (#978).

Builds a container image from source by running
``gcr.io/kaniko-project/executor`` as a one-shot Kubernetes Job in the
platform namespace, then pushes the result to the app's registry. Kaniko
needs no Docker daemon and no privileged container — it unpacks the build
context, executes the Dockerfile, and pushes layers directly to the
registry from userspace.

This driver is cloud-agnostic: it renders + spawns the Job through the
target cluster's ``ClusterDriver`` (the same ``apply_manifests`` /
``get_workload_status`` surface the deploy path uses) and is told which
ServiceAccount to run under. The caller (the ``build_image`` activity)
owns the cloud-specific prep — minting the registry-push identity role and
binding it to that ServiceAccount — so registry auth happens transparently
via the pod's projected credentials (IRSA on EKS / Workload Identity on
GKE / federated identity on AKS), with no static keys mounted.

Registry auth: kaniko ships the cloud credential helpers (ECR/GCR/ACR) and
auto-detects the registry host from ``--destination``, so a pod whose
ServiceAccount is bound to a push-scoped role authenticates without any
docker config Secret.

The pushed digest is NOT read here — boto3-less, the driver can't query
the registry. The activity reads the digest from the
``ImageRegistryDriver`` after a successful build (single source of truth,
reusing the existing ``list_tags`` surface).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from _sdk._telemetry import driver_op, maybe_heartbeat
from _sdk.build import BuildResult, BuildSpec

# Pinned kaniko executor. Pin (not :latest) so a build is reproducible and
# a surprise upstream release can't change build behavior under us. Override
# via the driver's ``image`` arg for an air-gapped mirror.
KANIKO_IMAGE = "gcr.io/kaniko-project/executor:v1.23.2"

# Namespace the build Job runs in — the platform namespace that
# bring_into_management provisions on every managed cluster (same one the
# keep-alive agent + agent tasks use). Always exists by build time, and the
# control-plane ClusterRole already grants jobs + serviceaccounts there.
BUILD_NAMESPACE = "astrolift-system"

# kaniko in-process push retries (exponential backoff). Sized to span the
# IRSA trust-policy propagation window for a freshly-minted build role —
# the push re-resolves the AssumeRoleWithWebIdentity credential each attempt.
_PUSH_RETRY = 6

# Pod-level backstop: a fresh pod re-runs the whole web-identity chain. Small
# — push-retry handles the common propagation case in-process; this only
# matters if the entire pod's projected token needs a clean re-assume.
_BACKOFF_LIMIT = 2

# How much of the build pod's output to carry back on a failure. Enough
# to hold a clone error, a Dockerfile step failure and its context;
# small enough to sit in an error message and a Deployment row.
_FAILURE_LOG_LINES = 100

# Modest resource floor so the kaniko pod schedules without starving the
# node; the limit gives a real build headroom without being unbounded.
_BUILD_RESOURCES = {
    "requests": {"cpu": "250m", "memory": "512Mi"},
    "limits": {"memory": "4Gi"},
}


def kaniko_context(source_uri: str) -> str:
    """Translate a platform ``source_uri`` into a kaniko ``--context`` value.

    The activity hands us ``git+https://<host>/<path>#<ref>`` (or a bare
    ``git+https://<host>/<path>``). Kaniko's git build-context wants the
    ``git://<host>/<path>.git#<ref>`` form; it clones over HTTPS for public
    repos with no credentials. The scheme is rewritten, a ``.git`` suffix
    ensured, and the ``#<ref>`` fragment (branch ref or commit sha) is
    preserved verbatim.
    """
    body = source_uri.strip()
    for prefix in ("git+https://", "git+http://", "git+", "https://", "http://", "git://"):
        if body.startswith(prefix):
            body = body[len(prefix) :]
            break
    repo, sep, ref = body.partition("#")
    repo = repo.rstrip("/")
    if repo and not repo.endswith(".git"):
        repo += ".git"
    context = f"git://{repo}"
    if sep and ref:
        context += f"#{ref}"
    return context


def render_build_service_account(*, name: str, namespace: str, role_arn: str) -> dict[str, Any]:
    """ServiceAccount the kaniko pod runs under.

    Annotated with the cloud's workload-identity role ARN so the pod-identity
    webhook injects push credentials. ``role_arn`` empty → no annotation
    (a cluster whose registry needs no per-pod identity, e.g. an in-cluster
    OCI registry the node can already reach)."""
    annotations = {"eks.amazonaws.com/role-arn": role_arn} if role_arn else {}
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"astrolift.io/managed-by": "platform", "astrolift.io/component": "build"},
            **({"annotations": annotations} if annotations else {}),
        },
    }


def git_secret_name(job_name: str) -> str:
    """Name of the per-build Secret carrying the clone credential."""

    return f"{job_name}-git"[:253]


def render_git_credentials_secret(
    *,
    name: str,
    namespace: str,
    username: str,
    password: str,
) -> dict[str, Any]:
    """Secret holding the git clone credential for a private source repo.

    kaniko's git build context reads ``GIT_USERNAME`` / ``GIT_PASSWORD``
    and turns them into HTTP basic auth (``pkg/buildcontext/git.go``),
    which is the form GitHub documents for an App installation token:
    ``x-access-token`` as the user, the token as the password. A PAT
    works through the same pair.

    A Secret rather than the clone URL because the URL is rendered into
    the Job's ``args``, logged by the activity, and readable by anyone
    who can get the Job -- a credential does not belong in any of those.
    """

    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"astrolift.io/managed-by": "platform", "astrolift.io/component": "build"},
        },
        "type": "Opaque",
        "stringData": {"GIT_USERNAME": username, "GIT_PASSWORD": password},
    }


def render_kaniko_job(
    *,
    job_name: str,
    namespace: str,
    service_account: str,
    context: str,
    dockerfile: str,
    destination: str,
    context_sub_path: str = "",
    build_args: dict[str, str] | None = None,
    image: str = KANIKO_IMAGE,
    git_secret: str = "",
) -> dict[str, Any]:
    """Render the one-shot kaniko build Job.

    ``--single-snapshot`` keeps the layer count down for a faster push.
    ``--push-retry`` re-attempts the registry push with backoff, re-resolving
    the cloud credential each time — this is what absorbs the IRSA
    eventual-consistency window: a brand-new app's build SA + push role are
    minted seconds before this pod runs, and the first
    ``AssumeRoleWithWebIdentity`` can 401 until the role's OIDC trust
    propagates (~tens of seconds); push-retry keeps re-resolving until it
    sticks instead of failing the whole build. ``--context-sub-path`` scopes
    the build into a monorepo subdir (omitted for a root context).
    ``backoffLimit`` gives a pod-level backstop (a fresh pod re-runs the full
    credential chain) for the rare case the in-process retry isn't enough.
    """
    args = [
        f"--context={context}",
        f"--dockerfile={dockerfile}",
        f"--destination={destination}",
        "--single-snapshot",
        f"--push-retry={_PUSH_RETRY}",
    ]
    if context_sub_path and context_sub_path != ".":
        args.append(f"--context-sub-path={context_sub_path}")
    for key in sorted(build_args or {}):
        args.append(f"--build-arg={key}={build_args[key]}")

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "astrolift.io/managed-by": "platform",
                "astrolift.io/component": "build",
                "astrolift.dev/app": job_name,
            },
        },
        "spec": {
            "backoffLimit": _BACKOFF_LIMIT,
            "completions": 1,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {
                    "labels": {
                        "astrolift.io/managed-by": "platform",
                        "astrolift.io/component": "build",
                        "astrolift.dev/app": job_name,
                    }
                },
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": service_account,
                    "containers": [
                        {
                            "name": "kaniko",
                            "image": image,
                            "args": args,
                            "resources": _BUILD_RESOURCES,
                            # Clone credential by reference only -- never
                            # in args, which are logged and readable off
                            # the Job (#1685).
                            **({"envFrom": [{"secretRef": {"name": git_secret}}]} if git_secret else {}),
                        }
                    ],
                },
            },
        },
    }


def _job_name(build_id: str) -> str:
    """Deterministic, DNS-label-safe (≤63) Job name for a build."""
    import hashlib
    import re

    cleaned = re.sub(r"[^a-z0-9-]+", "-", build_id.lower()).strip("-")
    base = f"astrolift-build-{cleaned}"
    if len(base) <= 63:
        return base.rstrip("-")
    digest = hashlib.sha256(build_id.encode("utf-8")).hexdigest()[:8]
    return base[:54].rstrip("-") + "-" + digest


class KanikoBuildDriver:
    """BuildDriver that runs kaniko as a Job on the target cluster."""

    def __init__(
        self,
        *,
        cluster_driver: Any,
        cluster_slug: str,
        service_account: str,
        service_account_role_arn: str = "",
        namespace: str = BUILD_NAMESPACE,
        build_id: str = "",
        image: str = KANIKO_IMAGE,
        git_username: str = "",
        git_password: str = "",
        poll_interval_seconds: float = 10.0,
        timeout_seconds: float = 1800.0,
        sleep: Any = time.sleep,
        clock: Any = time.monotonic,
        log_observer: Any = None,
    ) -> None:
        self._cluster_driver = cluster_driver
        self._cluster_slug = cluster_slug
        self._service_account = service_account
        self._sa_role_arn = service_account_role_arn
        self._namespace = namespace
        self._build_id = build_id
        self._image = image
        self._git_username = git_username
        self._git_password = git_password
        self._poll = poll_interval_seconds
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._clock = clock
        self._log_observer = log_observer
        self._previous_output: list[str] = []
        self._redactions: list[str] = [git_password] if git_password else []
        self._log_read_warning = False

    @driver_op(cloud="k8s_native", driver="build", audit=True, sensitive_kind="build.run")
    def build(self, spec: BuildSpec, repo: str, tag: str) -> BuildResult:
        self._redactions.extend(str(v) for v in spec.build_args.values() if v)
        destination = f"{repo}:{tag}"
        job_name = _job_name(self._build_id or f"{repo}-{tag}")
        # A private repo needs a clone credential; a public one must not
        # get a Secret it does not use (#1685).
        secret_name = git_secret_name(job_name) if self._git_password else ""
        manifests = [
            render_build_service_account(
                name=self._service_account,
                namespace=self._namespace,
                role_arn=self._sa_role_arn,
            ),
        ]
        if secret_name:
            manifests.append(
                render_git_credentials_secret(
                    name=secret_name,
                    namespace=self._namespace,
                    username=self._git_username or "x-access-token",
                    password=self._git_password,
                )
            )
        manifests.append(
            render_kaniko_job(
                job_name=job_name,
                namespace=self._namespace,
                service_account=self._service_account,
                context=kaniko_context(spec.source_uri),
                dockerfile=spec.dockerfile_path or "Dockerfile",
                destination=destination,
                context_sub_path=spec.context_path or "",
                build_args=dict(spec.build_args or {}),
                image=self._image,
                git_secret=secret_name,
            )
        )

        started = self._clock()
        try:
            apply_result = self._cluster_driver.apply_manifests(
                self._cluster_slug, self._namespace, manifests
            )
            if not getattr(apply_result, "ok", False):
                errors = apply_result.summary() if hasattr(apply_result, "summary") else ["apply failed"]
                return BuildResult(
                    success=False,
                    image_uri=destination,
                    digest="",
                    duration_seconds=self._clock() - started,
                    errors=[str(e) for e in errors],
                )

            outcome = self._poll_to_completion(job_name, started)
            return BuildResult(
                success=outcome["success"],
                image_uri=destination,
                digest="",
                duration_seconds=self._clock() - started,
                errors=outcome["errors"],
            )
        finally:
            # The Job self-deletes on its TTL; the Secret would outlive
            # it, so a short-lived credential would sit in the namespace
            # indefinitely. Removed on every exit, success or not.
            self._delete_git_secret(secret_name)

    def _delete_git_secret(self, secret_name: str) -> None:
        if not secret_name:
            return
        ref = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": secret_name, "namespace": self._namespace},
        }
        try:
            self._cluster_driver.delete_manifests(self._cluster_slug, self._namespace, [ref])
        except Exception:
            # Cleanup must never mask the build's own outcome. The Secret
            # holds a short-lived token that expires on its own.
            logging.getLogger(__name__).warning(
                "could not delete build git credential secret %s", secret_name, exc_info=True
            )

    def cancel(self, build_id: str) -> None:
        job_name = _job_name(build_id)
        job_ref = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": job_name, "namespace": self._namespace},
        }
        self._cluster_driver.delete_manifests(self._cluster_slug, self._namespace, [job_ref])

    def _poll_to_completion(self, job_name: str, started: float) -> dict[str, Any]:
        """Block until the build Job reaches a terminal state or times out.

        Reads the Job's ``Complete`` / ``Failed`` conditions (the same shape
        ``K8sJobSpawner.status`` keys on). Heartbeats each tick so a long
        cold build doesn't trip the activity's heartbeat timeout.
        """
        while True:
            maybe_heartbeat(f"build {job_name}")
            try:
                status = self._cluster_driver.get_workload_status(
                    self._cluster_slug, self._namespace, "Job", job_name
                )
                conditions = status.conditions or []
            except Exception as exc:
                # A transient read failure shouldn't abort a build that may
                # still be running — keep polling until the deadline.
                conditions = []
                if self._clock() - started < self._timeout:
                    self._sleep(self._poll)
                    continue
                return {"success": False, "errors": [f"status read failed: {exc}"]}

            self._capture_output(job_name)
            if _condition_true(conditions, "Complete"):
                return {"success": True, "errors": []}
            if _condition_true(conditions, "Failed"):
                reason = _condition_message(conditions, "Failed") or "kaniko build failed"
                return {"success": False, "errors": [reason, *self._failure_logs(job_name)]}

            if self._clock() - started >= self._timeout:
                return {
                    "success": False,
                    "errors": [
                        f"build timed out after {self._timeout:.0f}s",
                        *self._failure_logs(job_name),
                    ],
                }
            self._sleep(self._poll)

    def _capture_output(self, job_name: str) -> None:
        if self._log_observer is None:
            return
        reader = getattr(self._cluster_driver, "read_job_pod_logs", None)
        try:
            if reader is None:
                raise RuntimeError("cluster driver cannot read build pod logs")
            text = reader(self._cluster_slug, self._namespace, job_name, tail_lines=1000) or ""
        except Exception:
            if not self._log_read_warning:
                self._log_observer("[capture warning] Build output unavailable; subsequent polls will retry.")
                self._log_read_warning = True
            return
        lines = text.splitlines()
        overlap = min(len(self._previous_output), len(lines))
        while overlap and self._previous_output[-overlap:] != lines[:overlap]:
            overlap -= 1
        if lines and self._previous_output and not overlap:
            self._log_observer(
                "[capture warning] No overlap with previous tail; output may contain gaps or repeated lines."
            )
        elif lines and not self._previous_output and len(lines) >= 1000:
            self._log_observer(
                "[capture warning] Initial output tail reached 1000 lines; earlier output may be unavailable."
            )
        new_output = "\n".join(lines[overlap:])
        for secret in self._redactions:
            new_output = new_output.replace(secret, "[redacted]")
        if new_output:
            self._log_observer(new_output)
        self._previous_output = lines

    def _failure_logs(self, job_name: str) -> list[str]:
        """The build pod's own output, for a failure that has none.

        A failed Job reports "Job has reached the specified backoff
        limit" and nothing else, so every build failure read the same
        (#1686) -- and on a private-endpoint cluster the operator cannot
        go and look either. What actually went wrong is in the pod.

        Strictly diagnostic: any problem reading the logs degrades to a
        note in the returned list, because a build that failed must not
        be reported as failing for a second, invented reason.
        """

        reader = getattr(self._cluster_driver, "read_job_pod_logs", None)
        if reader is None:
            return []
        try:
            text = reader(self._cluster_slug, self._namespace, job_name, tail_lines=_FAILURE_LOG_LINES)
        except Exception as exc:
            return [f"build pod logs unavailable: {exc}"]
        text = (text or "").strip()
        for secret in self._redactions:
            text = text.replace(secret, "[redacted]")
        return [f"build pod logs (last {_FAILURE_LOG_LINES} lines):\n{text}"] if text else []


def _condition_true(conditions: list[dict[str, Any]], cond_type: str) -> bool:
    return any(str(c.get("type")) == cond_type and str(c.get("status")) == "True" for c in conditions)


def _condition_message(conditions: list[dict[str, Any]], cond_type: str) -> str:
    for c in conditions:
        if str(c.get("type")) == cond_type and str(c.get("status")) == "True":
            return str(c.get("message") or c.get("reason") or "")
    return ""
