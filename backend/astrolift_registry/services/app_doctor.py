"""App doctor (#1550) — verify everything an app needs is actually built.

Every onboarding incident in the 2026-08-21 sweep was a dependency that
LOOKED provisioned but wasn't usable: a manifest that parsed nowhere, a
push-role trust that never matched, a DNS record that existed but
answered nothing, a Cognito callback nobody registered. ``autowire_state``
covers the repo wiring; this service covers the rest, as read-only
checks an operator (or the reprovision flow) can trust.

Read-mostly: the manifest check runs the same idempotent resync the
Settings button does (drift heals in place; a staged draft is never
clobbered); every other check only reads.

Contract: :func:`run_app_doctor` NEVER raises. Each check returns a
:class:`DoctorCheck` with status ``pass`` / ``fail`` / ``warn`` /
``skip`` / ``unknown`` (``unknown`` = the probe itself errored — the
dependency's state is unverified, which is different from broken) plus a
human ``detail`` and, where one exists, the ``fix`` handle the UI maps
to an action (``resync_manifest`` / ``retry_autowire`` /
``rerun_onboarding`` / ``redeploy``).

Checks are deliberately dependency-injected (``probes``) so tests drive
every branch without AWS/GitHub/DNS; production callers use the default
probe set.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

Probe = Callable[..., object]

CHECK_MANIFEST = "manifest"
CHECK_AUTOWIRE = "autowire"
CHECK_REGISTRY = "registry_repo"
CHECK_PUSH_ROLE = "push_role"
CHECK_DNS = "dns"
CHECK_DEPLOYMENTS = "deployments"


@dataclasses.dataclass(slots=True, frozen=True)
class DoctorCheck:
    key: str
    status: str  # pass | fail | warn | skip | unknown
    detail: str
    fix: str = ""


@dataclasses.dataclass(slots=True, frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def healthy(self) -> bool:
        return all(c.status in ("pass", "skip") for c in self.checks)


def _check_manifest(app) -> DoctorCheck:
    """The repo manifest must parse AND match what the platform holds."""
    from astrolift_registry.services.manifest_sync import resync_app_manifest_from_repo

    if not (app.source_repo or "").strip():
        return DoctorCheck(CHECK_MANIFEST, "skip", "no source repo configured")
    # Same idempotent resync the Settings button runs: in_sync = clean,
    # applied = drift healed in place, diverged keeps the operator's
    # draft, anything else names the fetch/parse problem.
    result = resync_app_manifest_from_repo(app)
    if result.status == "in_sync":
        return DoctorCheck(CHECK_MANIFEST, "pass", "repo manifest parses and matches the registration")
    if result.status == "applied":
        return DoctorCheck(
            CHECK_MANIFEST,
            "pass",
            "repo manifest had drifted; resynced into the registration",
        )
    if result.status == "diverged":
        return DoctorCheck(
            CHECK_MANIFEST,
            "warn",
            "a staged manifest draft diverges from the repo; resolve the draft",
            fix="resync_manifest",
        )
    return DoctorCheck(
        CHECK_MANIFEST,
        "fail",
        result.error or "manifest fetch/parse failed",
        fix="resync_manifest",
    )


def _check_autowire(app) -> DoctorCheck:
    """Repo wiring per the recorded autowire outcome (#1108)."""
    state = app.autowire_state or {}
    if not state:
        return DoctorCheck(
            CHECK_AUTOWIRE,
            "warn",
            "autowire has never recorded an outcome for this app",
            fix="retry_autowire",
        )
    bad = sorted(
        step
        for step, outcome in state.items()
        if isinstance(outcome, dict) and outcome.get("status") not in ("ok", "skipped", None)
    )
    if bad:
        return DoctorCheck(
            CHECK_AUTOWIRE,
            "fail",
            f"autowire step(s) not ok: {', '.join(bad)}",
            fix="retry_autowire",
        )
    return DoctorCheck(CHECK_AUTOWIRE, "pass", "webhook, CI file, and deploy secret recorded ok")


def _check_registry(app) -> DoctorCheck:
    """The image repo + CI push role must be provisioned on the app row."""
    if not (app.registry_repo_uri or "").strip():
        return DoctorCheck(
            CHECK_REGISTRY,
            "fail",
            "no registry repo URI on the app; provisioning never completed",
            fix="rerun_onboarding",
        )
    return DoctorCheck(CHECK_REGISTRY, "pass", f"registry repo {app.registry_repo_uri}")


def _check_push_role(app) -> DoctorCheck:
    """push_role_ref must exist or CI renders a blank role-to-assume (#1219).

    Trust-policy CONTENT (ID-stamped OIDC subjects, #1532) self-heals on
    the next deploy via ensure_ci_push_role, so an existing ref passes.
    """
    if not (app.source_repo or "").strip():
        return DoctorCheck(CHECK_PUSH_ROLE, "skip", "no source repo; CI push role not applicable")
    if not (app.push_role_ref or "").strip():
        return DoctorCheck(
            CHECK_PUSH_ROLE,
            "fail",
            "no CI push role on the app; the rendered workflow cannot authenticate (#1219)",
            fix="retry_autowire",
        )
    return DoctorCheck(CHECK_PUSH_ROLE, "pass", f"push role {app.push_role_ref}")


def _check_dns(app, *, resolve: Probe) -> DoctorCheck:
    """Public hostnames must RESOLVE — record-exists is not enough (#1534)."""
    hostnames = [h for h in _public_hostnames(app) if h]
    if not hostnames:
        return DoctorCheck(CHECK_DNS, "skip", "no public workload; no hostname to resolve")
    dead = []
    for host in hostnames:
        try:
            answers = resolve(host)
        except Exception:  # noqa: BLE001 — resolver errors leave state unverified
            return DoctorCheck(CHECK_DNS, "unknown", f"could not probe DNS for {host}")
        if not answers:
            dead.append(host)
    if dead:
        return DoctorCheck(
            CHECK_DNS,
            "fail",
            "hostname(s) answer nothing: "
            + ", ".join(dead)
            + " — a record can exist and still answer nothing (#1534)",
            fix="redeploy",
        )
    return DoctorCheck(CHECK_DNS, "pass", f"{len(hostnames)} hostname(s) resolve")


def _check_deployments(app) -> DoctorCheck:
    """No stranded in-flight deployments (#1536); at least one has run."""
    from astrolift_lifecycle.models import Deployment

    rows = Deployment.objects.filter(registered_app=app, deleted_at__isnull=True)
    if not rows.exists():
        return DoctorCheck(
            CHECK_DEPLOYMENTS,
            "warn",
            "no deployment has ever run; push to the deploy branch or deploy manually",
            fix="redeploy",
        )
    stuck = rows.filter(status__in=("pending", "deploying", "redeploying")).count()
    # One in-flight row is a deploy in progress; more than one means
    # the supersede path (#1536) hasn't caught up — the next deploy will.
    if stuck > 1:
        return DoctorCheck(
            CHECK_DEPLOYMENTS,
            "warn",
            f"{stuck} in-flight deployment rows; the next deploy supersedes them (#1536)",
            fix="redeploy",
        )
    return DoctorCheck(CHECK_DEPLOYMENTS, "pass", "deployment history is clean")


def _public_hostnames(app) -> list[str]:
    """Managed hostnames for the app's public workloads, best-effort."""
    try:
        from astrolift_registry.schema.queries import _managed_hostnames_for_apps

        return [h for h in [_managed_hostnames_for_apps([app]).get(app.pk, "")] if h]
    except Exception:  # noqa: BLE001 — hostname derivation must not sink the doctor
        return []


def _default_resolve(host: str) -> list[str]:
    import socket

    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, 443)})
    except socket.gaierror:
        return []


_CHECKS: tuple[tuple[str, Callable], ...] = (
    (CHECK_MANIFEST, _check_manifest),
    (CHECK_AUTOWIRE, _check_autowire),
    (CHECK_REGISTRY, _check_registry),
    (CHECK_PUSH_ROLE, _check_push_role),
    (CHECK_DNS, _check_dns),
    (CHECK_DEPLOYMENTS, _check_deployments),
)


def run_app_doctor(app, *, resolve: Probe | None = None) -> DoctorReport:
    """Run every check; a crashed check reports ``unknown``, never raises."""
    resolve_fn = resolve if resolve is not None else _default_resolve
    out: list[DoctorCheck] = []
    for key, fn in _CHECKS:
        try:
            check = fn(app, resolve=resolve_fn) if key == CHECK_DNS else fn(app)
        except Exception:  # noqa: BLE001 — one broken probe must not sink the report
            logger.warning("app doctor: check %s crashed for %s", key, app.slug, exc_info=True)
            check = DoctorCheck(key, "unknown", "check crashed; state unverified")
        out.append(check)
    return DoctorReport(checks=tuple(out))
