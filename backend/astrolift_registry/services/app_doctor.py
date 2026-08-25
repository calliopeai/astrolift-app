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
import datetime as dt
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


#: How long a cached DNS answer is worth reporting (#1550).
#: A record that started answering an hour ago is news; one that started
#: answering a week ago and has not been re-probed is not evidence.
DNS_PROBE_MAX_AGE = dt.timedelta(hours=6)


def _check_dns(app) -> DoctorCheck:
    """Public hostnames must RESOLVE — record-exists is not enough (#1534).

    Answers from the cached probe on the app row, not from a live lookup.
    This runs on every app-detail page load, and `socket.getaddrinfo` on
    each hostname made the page's latency a function of DNS -- for a panel
    whose whole job is trust, slow and flaky is worse than honest and stale.

    So the probe runs on a schedule and this reports its result plus how old
    it is. A stale or absent probe is `unknown`, never `pass`: "nobody has
    checked recently" and "it resolves" are different answers.
    """
    hostnames = [h for h in _public_hostnames(app) if h]
    if not hostnames:
        return DoctorCheck(CHECK_DNS, "skip", "no public workload; no hostname to resolve")

    cached = _cached_dns_probe(app)
    if cached is None:
        return DoctorCheck(
            CHECK_DNS,
            "unknown",
            f"{len(hostnames)} hostname(s) not probed yet",
            fix="wait for the next DNS probe, or trigger one",
        )
    age, dead = cached
    if age > DNS_PROBE_MAX_AGE:
        return DoctorCheck(
            CHECK_DNS,
            "unknown",
            f"last DNS probe was {_humanise(age)} ago, too old to report",
            fix="trigger a DNS probe",
        )
    if dead:
        return DoctorCheck(
            CHECK_DNS,
            "fail",
            "hostname(s) answer nothing: "
            + ", ".join(sorted(dead))
            + f" — a record can exist and still answer nothing (#1534); probed {_humanise(age)} ago",
            fix="redeploy",
        )
    return DoctorCheck(
        CHECK_DNS,
        "pass",
        f"{len(hostnames)} hostname(s) resolve (probed {_humanise(age)} ago)",
    )


def _cached_dns_probe(app) -> tuple[dt.timedelta, list[str]] | None:
    """``(age, hostnames_that_answered_nothing)`` from the app row, or None.

    None means never probed. A malformed or unparseable record reads the same
    way, deliberately: a panel that reports `pass` off a record it could not
    parse is worse than one that admits it does not know.
    """
    raw = getattr(app, "dns_probe", None) or {}
    if not isinstance(raw, dict):
        return None
    stamp = raw.get("probed_at")
    if not stamp:
        return None
    try:
        probed_at = dt.datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if probed_at.tzinfo is None:
        probed_at = probed_at.replace(tzinfo=dt.UTC)

    dead = raw.get("unresolved") or []
    if not isinstance(dead, list):
        return None
    return (dt.datetime.now(dt.UTC) - probed_at, [str(h) for h in dead])


def _humanise(age: dt.timedelta) -> str:
    """Coarse and readable. An operator wants "3h", not "3:07:42.119"."""
    seconds = int(age.total_seconds())
    if seconds < 90:
        return f"{max(seconds, 0)}s"
    if seconds < 90 * 60:
        return f"{seconds // 60}m"
    if seconds < 48 * 3600:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


CHECK_IDENTITY = "identity"
CHECK_IMAGE = "image"


def _check_identity(app) -> DoctorCheck:
    """The platform can build a pod-identity annotation for this app.

    Nothing persists the binding: `_workload_identity_annotations` computes it
    at deploy time from the *cluster's* `provider_config`. So the check is
    whether that computation would produce anything -- and it is a real
    check, because the AWS branch returns `{}` when `account_id` is absent,
    which means pods deploy with no identity at all and every AWS call they
    make fails at runtime, from a cluster row that looks configured.
    """
    cluster = getattr(app, "default_tenant_cluster", None)
    if cluster is None:
        return DoctorCheck(CHECK_IDENTITY, "skip", "no cluster bound, so no pod identity applies yet")

    from core.app_deploy import _workload_identity_annotations

    annotations = _workload_identity_annotations(
        plugin_slug=str(getattr(getattr(cluster, "provider_plugin", None), "slug", "") or ""),
        provider_config=getattr(cluster, "provider_config", None) or {},
        auth_config=getattr(cluster, "auth_config", None) or {},
        role_name=f"astrolift-{app.slug}",
    )
    if not annotations:
        return DoctorCheck(
            CHECK_IDENTITY,
            "fail",
            f"cluster {getattr(cluster, 'slug', '?')!r} is missing the provider config "
            f"needed to build a pod identity, so pods deploy with none",
            fix="set the cluster's account/project identifier and reprovision",
        )
    return DoctorCheck(CHECK_IDENTITY, "pass", "pod identity resolvable")


def _check_image(app) -> DoctorCheck:
    """The latest deployment pins its image by digest.

    Registry presence needs a live call and is not claimed here. An
    *unpinned* tag is still a fail: a deployment on a floating tag cannot be
    reproduced whatever the registry currently holds, and that is knowable
    from the row.
    """
    from astrolift_lifecycle.models import Deployment

    latest = (
        Deployment.objects.filter(registered_app_id=app.pk, deleted_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if latest is None:
        return DoctorCheck(CHECK_IMAGE, "skip", "never deployed")
    if not (latest.image_digest or "").strip():
        # `warn`, not `fail`, to match how the platform already treats this:
        # `supply_chain_gate` reports an unpinned digest as a failed *scan*
        # and lets `fail_open_on_scanner_error` decide, so it is notable
        # rather than broken. A doctor that called it broken would disagree
        # with the gate about the same deployment.
        return DoctorCheck(
            CHECK_IMAGE,
            "warn",
            "the latest deployment references an unpinned image tag, so it "
            "cannot be reproduced and cannot be scanned by digest",
            fix="redeploy to pin the image by digest",
        )
    return DoctorCheck(CHECK_IMAGE, "pass", "pinned by digest (registry presence not verified from here)")


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
    (CHECK_IDENTITY, _check_identity),
    (CHECK_IMAGE, _check_image),
    (CHECK_DEPLOYMENTS, _check_deployments),
)


def run_app_doctor(app) -> DoctorReport:
    """Run every check; a crashed check reports ``unknown``, never raises.

    No live network call anywhere in here now (#1550). The DNS check reads
    the cached probe on the app row, so this is safe on a page load and its
    latency is the sum of a few queries rather than a DNS round-trip per
    hostname. `probe_app_dns` is what refreshes the cache, on a schedule.
    """
    out: list[DoctorCheck] = []
    for key, fn in _CHECKS:
        try:
            check = fn(app)
        except Exception:  # noqa: BLE001 — one broken probe must not sink the report
            logger.warning("app doctor: check %s crashed for %s", key, app.slug, exc_info=True)
            check = DoctorCheck(key, "unknown", "check crashed; state unverified")
        out.append(check)
    return DoctorReport(checks=tuple(out))


def probe_app_dns(app, *, resolve: Probe | None = None) -> dict:
    """Resolve the app's public hostnames and cache the result on the row.

    The live half, moved out of the doctor and onto a schedule. Returns the
    record it wrote so a caller can log a change.

    A resolver error records the hostname as unresolved rather than skipping
    it, and that is the conservative direction: a probe that cannot resolve
    a name has not established that the name works, and reporting `pass`
    off a failed lookup is the #1534 failure again one level up.
    """
    hostnames = [h for h in _public_hostnames(app) if h]
    resolve_fn = resolve if resolve is not None else _default_resolve

    unresolved: list[str] = []
    for host in hostnames:
        try:
            if not resolve_fn(host):
                unresolved.append(host)
        except Exception:  # noqa: BLE001 - a failed lookup is not a pass
            unresolved.append(host)

    record = {
        "probed_at": dt.datetime.now(dt.UTC).isoformat(),
        "unresolved": sorted(unresolved),
        "checked": sorted(hostnames),
    }
    app.dns_probe = record
    app.save(update_fields=["dns_probe", "updated_at", "version"])
    return record
