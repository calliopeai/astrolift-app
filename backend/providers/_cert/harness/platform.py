"""The seam between the runner and the platform (spec 41 §2, spec 43 §3.2).

The runner drives Astrolift the way a customer does: through the CLI and the
GraphQL API, never through the ORM and never through a cloud SDK. That is the
dogfooding spec 41 asked for, and it is also the only reason a green cell means
anything -- a harness that reaches around the API certifies code paths nobody
uses. The single exception is VERIFY-CLEAN, which by definition has to look at
the cloud itself; that is what the orphan scanners are for, and they arrive as
their own injected handle rather than through this protocol.

``PlatformClient`` is a Protocol so the whole runner can be exercised offline
against a fake. ``CliPlatformClient`` is the credentialed implementation.

**Which surface each operation uses, and why it is not all CLI.** The CLI is the
preferred surface wherever it covers the operation; the gaps below are real
CLI gaps and each one is worth a ticket.

===========================  ==========  ==================================
operation                    surface     note
===========================  ==========  ==================================
register_app                 GraphQL     ``astro app register`` reads an
                                         ``[app] slug`` / ``display_name``
                                         table; the campaign manifests use
                                         the backend parser's top-level
                                         ``name``. Campaign blocker B1
                                         (manifest schema unification) is
                                         still open, so the CLI cannot read
                                         these manifests as written.
provision_managed_service    GraphQL     No CLI command exists for an
                                         app-scoped managed service.
                                         ``astro project resources add``
                                         covers project-scoped ones only.
list_managed_services        GraphQL     Same gap.
get_app                      CLI         ``astro app show --json``
list_workloads               CLI         ``astro app pods --json``
deploy                       CLI         ``astro app deploy --wait``, which
                                         polls to a terminal state
rollback                     CLI         ``astro app rollback --yes``
deregister                   CLI         ``astro app deregister --yes``
probe                        neither     the app's own public URL, which is
                                         the thing a customer actually hits
===========================  ==========  ==================================

Note also that ``ProvisionManagedServiceInput`` has no ``tags`` field at all, so
a manifest's ``[campaign.tags]`` has no route into the platform from any
surface. The scanners therefore find campaign residue by app name only. See
``verification/README.md`` finding 1.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from _cert.harness.cell import Cell


class PlatformError(RuntimeError):
    """The platform surface itself failed: a non-zero CLI exit, a transport
    error, an unparseable response. Distinct from an assertion, which is the
    platform answering clearly with the wrong answer."""


@dataclass(frozen=True)
class DeploymentObservation:
    id: str
    status: str
    image_tag: str
    environment_name: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status.lower() in {"succeeded", "success", "deployed", "complete", "completed"}


@dataclass(frozen=True)
class AppObservation:
    slug: str
    provisioning_status: str = ""
    is_active: bool = True
    url: str = ""
    latest_deployment: DeploymentObservation | None = None


@dataclass(frozen=True)
class WorkloadObservation:
    name: str
    desired_replicas: int = 0
    ready_replicas: int = 0
    images: tuple[str, ...] = ()
    """Every image running under this workload right now. More than one means a
    rollout is still in flight, which is how VERIFY-UPD sees an un-retired old
    revision."""


@dataclass(frozen=True)
class ManagedServiceObservation:
    name: str
    kind: str
    variant: str
    status: str
    status_error: str = ""

    @property
    def is_active(self) -> bool:
        return self.status.lower() == "active"

    @property
    def is_terminal(self) -> bool:
        return self.status.lower() in {"active", "failed", "error", "deprovisioned"}


@dataclass(frozen=True)
class ProbeResult:
    """One HTTP request against the app's own public URL."""

    status_code: int = 0
    body: str = ""
    tls_verified: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and not self.error


class PlatformClient(Protocol):
    """Everything the runner needs, and nothing that reaches around the API."""

    def register_app(self, cell: Cell) -> None: ...

    def get_app(self, slug: str) -> AppObservation | None:
        """``None`` when the platform does not show the app to this caller.

        Soft delete means a deregistered app's row survives; what matters to the
        cycle is that the API no longer serves it, which is exactly what this
        returns."""

    def provision_managed_service(self, slug: str, environment: str, kind: str, name: str, variant: str) -> None: ...

    def list_managed_services(self, slug: str, environment: str) -> Sequence[ManagedServiceObservation]: ...

    def list_workloads(self, slug: str) -> Sequence[WorkloadObservation]: ...

    def deploy(self, slug: str, environment: str, image_tag: str) -> DeploymentObservation:
        """Start a deploy and block until it reaches a terminal state."""

    def rollback(self, slug: str, environment: str) -> DeploymentObservation: ...

    def deregister(self, slug: str) -> None:
        """Start teardown. Returns as soon as the platform accepts it; the
        runner polls ``get_app`` for completion, because deregister is a
        workflow with a grace window rather than a synchronous call."""

    def probe(self, url: str, path: str) -> ProbeResult: ...


# ---- the credentialed implementation -----------------------------------------


_REGISTER_APP = """
mutation($input: RegisterAppInput!) {
  registerApp(input: $input) { ok errors { code message field } data { id slug } }
}
"""

_PROVISION_MANAGED_SERVICE = """
mutation($input: ProvisionManagedServiceInput!) {
  provisionManagedService(input: $input) {
    ok errors { code message field } data { id name kind variant status }
  }
}
"""

_MANAGED_SERVICES = """
query($appSlug: String!, $environmentName: String) {
  astroliftManagedServices(appSlug: $appSlug, environmentName: $environmentName) {
    name kind variant status statusError
  }
}
"""


@dataclass
class CliPlatformClient:
    """``astro`` for what the CLI covers, GraphQL for the rest.

    Untested against a live control plane -- Phase 2 is its first real run, the
    same posture as the ``Live*Inventory`` adapters. The runner logic it feeds
    is covered offline with a fake.
    """

    project_id: str
    source_repo: str
    api_url: str = field(default_factory=lambda: os.environ.get("ASTROLIFT_API_URL", ""))
    token: str = field(default_factory=lambda: os.environ.get("ASTROLIFT_TOKEN", ""))
    astro_bin: str = "astro"
    timeout_s: int = 2400
    """Generous because ``astro app deploy --wait`` legitimately runs for tens
    of minutes on a first provision. A tight timeout here reports a platform
    defect that is really the harness giving up."""

    # -- transport -----------------------------------------------------------

    def _astro(self, *args: str, expect_json: bool = True) -> Any:
        command = [self.astro_bin, *args]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
            check=False,
        )
        if completed.returncode != 0:
            raise PlatformError(
                f"`{shlex.join(command)}` exited {completed.returncode}: "
                f"{(completed.stderr or completed.stdout).strip()}"
            )
        if not expect_json:
            return completed.stdout
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise PlatformError(f"`{shlex.join(command)}` did not return JSON: {completed.stdout[:400]}") from exc

    def _graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        if not self.api_url or not self.token:
            raise PlatformError("ASTROLIFT_API_URL and ASTROLIFT_TOKEN must be set for the GraphQL surface")
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps({"query": query, "variables": dict(variables)}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PlatformError(f"GraphQL transport failed: {exc}") from exc
        if payload.get("errors"):
            raise PlatformError(f"GraphQL errors: {payload['errors']}")
        return payload.get("data") or {}

    @staticmethod
    def _unwrap(result: Mapping[str, Any], field_name: str) -> dict[str, Any]:
        """Read a ``MutationResult`` envelope. ``ok: false`` is a platform
        refusal with a reason, which is a very different thing from a transport
        failure and has to keep its message."""
        envelope = result.get(field_name) or {}
        if not envelope.get("ok"):
            errors = envelope.get("errors") or []
            reason = "; ".join(f"{e.get('code', '')} {e.get('message', '')}".strip() for e in errors)
            raise PlatformError(f"{field_name} refused: {reason or 'no error detail returned'}")
        return envelope.get("data") or {}

    # -- operations ----------------------------------------------------------

    def register_app(self, cell: Cell) -> None:
        self._unwrap(
            self._graphql(
                _REGISTER_APP,
                {
                    "input": {
                        "projectId": self.project_id,
                        "name": cell.app_name,
                        "slug": cell.app_name,
                        "sourceKind": "github",
                        "sourceRepo": self.source_repo,
                    }
                },
            ),
            "registerApp",
        )

    def get_app(self, slug: str) -> AppObservation | None:
        try:
            payload = self._astro("--json", "app", "show", slug)
        except PlatformError as exc:
            if "not found" in str(exc).lower():
                return None
            raise
        app = payload.get("app") or payload.get("App") or {}
        if not app:
            return None
        environments = payload.get("environments") or payload.get("Environments") or []
        latest = app.get("latestDeployment")
        return AppObservation(
            slug=str(app.get("slug", slug)),
            provisioning_status=str(app.get("provisioningStatus", "")),
            is_active=bool(app.get("isActive", True)) and not app.get("isArchived"),
            url=str(next((env.get("url") for env in environments if env.get("url")), "")),
            latest_deployment=(
                DeploymentObservation(
                    id=str(latest.get("id", "")),
                    status=str(latest.get("status", "")),
                    image_tag=str(latest.get("imageTag", "")),
                    environment_name=str(latest.get("environmentName", "")),
                )
                if latest
                else None
            ),
        )

    def provision_managed_service(self, slug: str, environment: str, kind: str, name: str, variant: str) -> None:
        self._unwrap(
            self._graphql(
                _PROVISION_MANAGED_SERVICE,
                {
                    "input": {
                        "appSlug": slug,
                        "environmentName": environment,
                        "kind": kind,
                        "name": name,
                        "variant": variant,
                    }
                },
            ),
            "provisionManagedService",
        )

    def list_managed_services(self, slug: str, environment: str) -> Sequence[ManagedServiceObservation]:
        data = self._graphql(_MANAGED_SERVICES, {"appSlug": slug, "environmentName": environment})
        return [
            ManagedServiceObservation(
                name=str(row.get("name", "")),
                kind=str(row.get("kind", "")),
                variant=str(row.get("variant", "")),
                status=str(row.get("status", "")),
                status_error=str(row.get("statusError", "")),
            )
            for row in data.get("astroliftManagedServices") or []
        ]

    def list_workloads(self, slug: str) -> Sequence[WorkloadObservation]:
        pods = self._astro("--json", "app", "pods", slug)
        rows = pods if isinstance(pods, list) else pods.get("pods") or []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for pod in rows:
            grouped.setdefault(str(pod.get("workload") or pod.get("workloadSlug") or ""), []).append(pod)
        return [
            WorkloadObservation(
                name=name,
                desired_replicas=len(members),
                ready_replicas=sum(1 for pod in members if pod.get("ready")),
                images=tuple(sorted({str(pod.get("image", "")) for pod in members if pod.get("image")})),
            )
            for name, members in sorted(grouped.items())
        ]

    def deploy(self, slug: str, environment: str, image_tag: str) -> DeploymentObservation:
        self._astro(
            "app",
            "deploy",
            "--app",
            slug,
            "--env",
            environment,
            "--image-tag",
            image_tag,
            "--wait",
            expect_json=False,
        )
        return self._latest_or_fail(slug, "deploy")

    def rollback(self, slug: str, environment: str) -> DeploymentObservation:
        self._astro("app", "rollback", "--app", slug, "--env", environment, "--yes", expect_json=False)
        return self._latest_or_fail(slug, "rollback")

    def _latest_or_fail(self, slug: str, operation: str) -> DeploymentObservation:
        """``astro app deploy --wait`` prints progress rather than a record, so
        the deployment is read back from ``app show``."""
        app = self.get_app(slug)
        if app is None or app.latest_deployment is None:
            raise PlatformError(f"{operation} of {slug} left no deployment record on the app")
        return app.latest_deployment

    def deregister(self, slug: str) -> None:
        self._astro("app", "deregister", slug, "--yes", expect_json=False)

    def probe(self, url: str, path: str) -> ProbeResult:
        import ssl
        import urllib.error
        import urllib.request

        target = url.rstrip("/") + "/" + path.lstrip("/")
        # Default context: certificate verification stays ON. A probe that
        # disables it cannot assert the "ingress reachable + TLS" half of
        # VERIFY-UP, which is one of the things the campaign is certifying.
        context = ssl.create_default_context()
        try:
            with urllib.request.urlopen(target, timeout=60, context=context) as response:
                return ProbeResult(
                    status_code=response.status,
                    body=response.read().decode(errors="replace"),
                    tls_verified=target.startswith("https://"),
                )
        except urllib.error.HTTPError as exc:
            return ProbeResult(
                status_code=exc.code,
                body=exc.read().decode(errors="replace"),
                tls_verified=target.startswith("https://"),
            )
        except Exception as exc:
            return ProbeResult(error=f"{type(exc).__name__}: {exc}")
