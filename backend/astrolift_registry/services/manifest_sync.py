"""
Resync an app's manifest from its source repo (#386).

The manifest editor in the UI already supports staging edits and
opening a PR, plus a destructive "sync from repo" that drops the
local draft and re-anchors. What this module adds is the safe,
diff-and-apply path the Settings landing's "Resync from source"
button calls:

1. Look up a usable ``SourceConnection`` for the registered app
   (same org, same source kind, not orphaned, active).
2. Fetch ``astrolift.toml`` from the deploy branch via
   ``astrolift_scm.providers.fetch_file``.
3. Parse + normalize the repo content. Compute the diff against the
   DB's persisted state (workloads / env / managed services /
   schedules).
4. When the DB has a staged draft AND the repo has new content,
   refuse with ``status="diverged"`` — clobbering the draft is the
   point of the existing destructive ``syncManifestFromRepo``
   mutation, not this one.
5. When the repo matches the DB, return ``status="in_sync"`` with
   empty changes.
6. Otherwise call ``persist_manifest`` to reconcile workloads +
   containers, update ``manifest_raw`` / ``manifest_hash`` /
   ``last_synced_hash`` / ``last_resync_at`` atomically, and return
   the per-bucket diff for the UI's summary toast.

The fetch is injectable via ``_FetchFn`` so tests can drive every
branch of the matrix without hitting the SCM provider — the
production code path always passes the default
``astrolift_scm.providers.fetch_file``.
"""

from __future__ import annotations

import dataclasses
import io
import logging
import zipfile
from collections.abc import Callable
from typing import Any

from _sdk.k8s_naming import app_namespace
from django.db import transaction
from django.utils import timezone

from astrolift_manifest.discover import (
    AgentFederationError,
    DiscoveredAgentManifest,
    DiscoveredAppManifest,
    scan_agent_manifests_with_skips,
    scan_app_manifests,
)
from astrolift_manifest.normalize import NormalizationDefaults, manifest_hash, normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_manifest.schema_detect import detect_toml_schema
from astrolift_manifest.types import (
    ManagedServiceManifest,
    NormalizedManifest,
    WorkloadManifest,
)
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from core.events import Event

log = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class ResyncChanges:
    """Per-bucket diff between the repo manifest and the DB.

    Workload buckets carry the slugs (= manifest name) of affected
    rows so the UI's summary toast can list them; env + schedules
    only carry counts because surfacing per-key diffs in a toast is
    noise.
    """

    workloads_added: list[str] = dataclasses.field(default_factory=list)
    workloads_removed: list[str] = dataclasses.field(default_factory=list)
    workloads_changed: list[str] = dataclasses.field(default_factory=list)
    env_keys_changed: int = 0
    managed_services_added: list[str] = dataclasses.field(default_factory=list)
    managed_services_removed: list[str] = dataclasses.field(default_factory=list)
    schedules_changed: int = 0

    @property
    def is_empty(self) -> bool:
        return (
            not self.workloads_added
            and not self.workloads_removed
            and not self.workloads_changed
            and self.env_keys_changed == 0
            and not self.managed_services_added
            and not self.managed_services_removed
            and self.schedules_changed == 0
        )


@dataclasses.dataclass(slots=True)
class ResyncResult:
    """Return shape from :func:`resync_app_manifest_from_repo`.

    ``status`` is one of:

    - ``in_sync``    — repo and DB are byte-identical (or normalize
                        to the same hash); nothing applied.
    - ``applied``    — repo has new content; ``persist_manifest``
                        reconciled the workloads + containers, and
                        ``manifest_raw`` / hashes / ``last_resync_at``
                        were updated atomically.
    - ``diverged``   — DB has a staged draft that would be clobbered
                        by the repo content; nothing applied.
    - ``fetch_failed`` — SCM fetch raised or returned ``None``; DB
                        unchanged. ``error`` carries the host-side
                        message so the UI can surface it.

    ``changes`` is populated on ``applied``; otherwise empty.

    ``env_names`` carries the ``[environments.*]`` section keys found
    in the repo manifest (e.g. ``["production", "staging"]``). The
    mutation layer uses this to bootstrap ``AppEnvironment`` rows when
    the app hasn't been provisioned yet.
    """

    status: str
    changes: ResyncChanges
    error: str | None = None
    env_names: list[str] = dataclasses.field(default_factory=list)


_FetchFn = Callable[[SourceConnection, str, str, str], "str | None"]


def _default_fetch(
    connection: SourceConnection,
    repo_full_name: str,
    path: str,
    ref: str,
) -> str | None:
    """Production fetch: thin wrapper over the SCM provider dispatch
    so the call site can be monkeypatched in tests without poking at
    the import-time symbol on the provider package."""
    from astrolift_scm.providers import fetch_file

    return fetch_file(
        connection,
        repo_full_name=repo_full_name,
        path=path,
        ref=ref,
    )


# DOCUMENTED EXCEPTION to the "platform writes are App-only" rule.
#
# The strict-write pickers (CI secrets, workflow dispatch, workflow-file
# write) resolve the org GitHub App and nothing else, via
# ``connection_resolver.resolve_connection(purpose=PLATFORM_REPO_WRITE)``.
# This picker deliberately keeps the App-first-then-OAuth-user-then-PAT
# preference because manifest read + TOML write-back + repo scan + the
# manifest-PR flow are all supported through an org-level ``github_pat``
# OR ``github_oauth_user`` connection — a heavily-tested path
# (test_resync_manifest / test_manifest_mutations / test_toml_writeback
# scaffold PAT and OAuth-user connections). Forcing App-only here would
# strand every org that onboarded without installing the GitHub App and
# regress those flows to ``fetch_failed``. The bug this refactor fixed
# was a *viewer-scoped personal* token used for a secret write, not an
# org-level connection used for a manifest op; this picker was never
# part of that defect. Tightening it to App-only is a separate,
# behaviour-changing migration (update the PAT/OAuth fixtures + accept
# that App-less orgs lose manifest sync) and is intentionally out of
# scope here.
_PLATFORM_OP_KINDS: dict[str, tuple[str, ...]] = {
    "github": (
        "github_app_install",
        "github_oauth_user",
        "github_pat",
    ),
    "gitlab": (
        "gitlab_oauth_user",
        "gitlab_pat",
    ),
}


def _pick_source_connection(app: RegisteredApp) -> SourceConnection | None:
    """Pick the org's connection for a manifest operation on ``app``'s
    source host (see the exception note above). App-first, then
    OAuth-user, then PAT; None when no usable row exists, which callers
    surface as ``fetch_failed``."""
    accepted = _PLATFORM_OP_KINDS.get(app.source_kind, ())
    if not accepted:
        return None
    rows = list(
        SourceConnection.objects.filter(
            organization_id=app.organization_id,
            kind__in=accepted,
            is_active=True,
            is_orphaned=False,
            deleted_at__isnull=True,
        )
    )
    if not rows:
        return None
    rank = {k: i for i, k in enumerate(accepted)}
    rows.sort(key=lambda r: (rank.get(r.kind, len(accepted)), r.pk))
    return rows[0]


def _normalize_text(raw_text: str) -> NormalizedManifest:
    return normalize(parse_raw(raw_text), defaults=NormalizationDefaults())


def _workloads_by_name(
    workloads: tuple[WorkloadManifest, ...],
) -> dict[str, WorkloadManifest]:
    return {w.name: w for w in workloads}


def _managed_services_by_name(
    services: tuple[ManagedServiceManifest, ...],
) -> dict[str, ManagedServiceManifest]:
    # ``name`` may be empty in the manifest shape — when so, fall
    # back to ``kind`` so the diff can still key something stable.
    out: dict[str, ManagedServiceManifest] = {}
    for m in services:
        key = m.name or m.kind
        out[key] = m
    return out


def _env_keys_changed(
    before: tuple[WorkloadManifest, ...],
    after: tuple[WorkloadManifest, ...],
) -> int:
    """Count of (workload, env_key) pairs whose value differs.

    A pair counts once whether the key was added, removed, or its
    value changed. Walking workloads + containers gives us a stable
    cross-product without needing the persisted Container rows.
    """
    before_env: dict[tuple[str, str, str], str] = {}
    for w in before:
        for c in w.containers:
            for k, v in c.env:
                before_env[(w.name, c.name, k)] = v

    after_env: dict[tuple[str, str, str], str] = {}
    for w in after:
        for c in w.containers:
            for k, v in c.env:
                after_env[(w.name, c.name, k)] = v

    changed = 0
    seen: set[tuple[str, str, str]] = set()
    for key, v in before_env.items():
        if after_env.get(key) != v:
            changed += 1
        seen.add(key)
    for key in after_env:
        if key in seen:
            continue
        # New key in the after-set: also a change.
        changed += 1
    return changed


def _schedules_changed(
    before: tuple[WorkloadManifest, ...],
    after: tuple[WorkloadManifest, ...],
) -> int:
    before_sched = {w.name: (w.schedule or "") for w in before if w.kind == "cronjob"}
    after_sched = {w.name: (w.schedule or "") for w in after if w.kind == "cronjob"}
    keys = set(before_sched) | set(after_sched)
    return sum(1 for k in keys if before_sched.get(k, "") != after_sched.get(k, ""))


def _workload_body_changed(a: WorkloadManifest, b: WorkloadManifest) -> bool:
    """Compare two normalized workloads for any body change.

    Containers are compared by their serialized env / image / command
    tuple — same fields the ``persist`` layer uses to decide whether
    an UPDATE is necessary.
    """
    if a.kind != b.kind:
        return True
    if a.is_public != b.is_public:
        return True
    if (a.schedule or "") != (b.schedule or ""):
        return True
    if a.replicas != b.replicas:
        return True
    if (a.cpu_request, a.cpu_limit, a.memory_request, a.memory_limit) != (
        b.cpu_request,
        b.cpu_limit,
        b.memory_request,
        b.memory_limit,
    ):
        return True
    if (a.hpa_min, a.hpa_max, a.hpa_target_cpu_pct) != (b.hpa_min, b.hpa_max, b.hpa_target_cpu_pct):
        return True
    if (a.storage_class, a.storage_size) != (b.storage_class, b.storage_size):
        return True
    # Static-site config (#1010) — a static_* edit must trigger a resync.
    if (
        a.static_build_command,
        a.static_output_dir,
        a.static_spa,
        a.static_index,
    ) != (
        b.static_build_command,
        b.static_output_dir,
        b.static_spa,
        b.static_index,
    ):
        return True
    # FaaS config (#987) — a faas_* edit must trigger a resync.
    if (
        a.faas_package_type,
        a.faas_runtime,
        a.faas_handler,
        a.faas_memory_mb,
        a.faas_timeout_seconds,
        a.faas_architecture,
        a.faas_public,
        a.faas_build_command,
        a.faas_output_dir,
    ) != (
        b.faas_package_type,
        b.faas_runtime,
        b.faas_handler,
        b.faas_memory_mb,
        b.faas_timeout_seconds,
        b.faas_architecture,
        b.faas_public,
        b.faas_build_command,
        b.faas_output_dir,
    ):
        return True

    a_containers = {c.name: c for c in a.containers}
    b_containers = {c.name: c for c in b.containers}
    if set(a_containers) != set(b_containers):
        return True
    for name, ac in a_containers.items():
        bc = b_containers[name]
        if (
            ac.is_primary != bc.is_primary
            or (ac.image_ref or "") != (bc.image_ref or "")
            or ac.dockerfile_path != bc.dockerfile_path
            or ac.build_context != bc.build_context
            or ac.port != bc.port
            or list(ac.command) != list(bc.command)
            or list(ac.args) != list(bc.args)
            or dict(ac.env) != dict(bc.env)
            or ac.healthcheck_kind != bc.healthcheck_kind
            or (ac.healthcheck_value or "") != (bc.healthcheck_value or "")
            or ac.healthcheck_port != bc.healthcheck_port
        ):
            return True
    return False


def _compute_changes(
    before: NormalizedManifest | None,
    after: NormalizedManifest,
) -> ResyncChanges:
    before_workloads = before.workloads if before is not None else ()
    before_msvc = before.managed_services if before is not None else ()

    bw = _workloads_by_name(before_workloads)
    aw = _workloads_by_name(after.workloads)

    added = sorted(name for name in aw if name not in bw)
    removed = sorted(name for name in bw if name not in aw)
    changed = sorted(name for name in aw if name in bw and _workload_body_changed(bw[name], aw[name]))

    bm = _managed_services_by_name(before_msvc)
    am = _managed_services_by_name(after.managed_services)
    msvc_added = sorted(name for name in am if name not in bm)
    msvc_removed = sorted(name for name in bm if name not in am)

    return ResyncChanges(
        workloads_added=added,
        workloads_removed=removed,
        workloads_changed=changed,
        env_keys_changed=_env_keys_changed(before_workloads, after.workloads),
        managed_services_added=msvc_added,
        managed_services_removed=msvc_removed,
        schedules_changed=_schedules_changed(before_workloads, after.workloads),
    )


def resync_app_manifest_from_repo(
    app: RegisteredApp,
    *,
    fetch: _FetchFn | None = None,
    ref: str = "",
) -> ResyncResult:
    """Re-fetch + reconcile ``app``'s manifest, and leave an audit trail.

    Thin wrapper over :func:`_resync_app_manifest_from_repo` that emits an
    event for every outcome the operator would want to know about (#1553).
    A refused resync used to exist only as a worker log line, so a deploy
    that silently rendered the stored manifest was indistinguishable from
    one that rendered a fresh one. ``in_sync`` is deliberately not emitted
    — every deploy resyncs, and "nothing changed" is not news.

    Emission never affects the result: an events-backend failure must not
    turn a successful resync into a failed one.
    """
    result = _resync_app_manifest_from_repo(app, fetch=fetch, ref=ref)
    if result.status != "in_sync":
        try:
            Event.emit(
                f"app.manifest_resync.{result.status}",
                {
                    "app_slug": app.slug,
                    "source_repo": app.source_repo,
                    "manifest_path": app.manifest_path,
                    "branch": app.deploy_branch or app.default_branch or "main",
                    "ref": ref or app.deploy_branch or app.default_branch or "main",
                    "error": result.error or "",
                },
                resource_kind="registered_app",
                resource_id=app.pk,
                registered_app_id=app.pk,
                organization_id=app.organization_id,
            )
        except Exception:  # noqa: BLE001 — audit is best-effort
            log.exception("failed to emit manifest resync event for app %s", app.pk)
    return result


def _clear_stale_bootstrap_failure(app: RegisteredApp) -> list[str]:
    """Retire a registration-time bootstrap failure the repo has fixed (#1692).

    ``manifest_bootstrap_status`` records why an app registered without
    its workloads, and the app detail page renders it as "This app
    registered without its workloads ... no services were created from
    it". It was written once, at registration, and never revisited -- so
    an app whose manifest was fixed and resynced, with its workloads
    materialised and a working source connection, kept telling the
    operator it had neither. The banner is a current-state signal (that
    is what it is for), not an audit record; the registration attempt
    itself stays in the audit log.

    Returns the fields to add to the caller's ``update_fields``.
    """

    if app.manifest_bootstrap_status in ("", "applied"):
        return []
    app.manifest_bootstrap_status = "applied"
    app.manifest_bootstrap_error = ""
    return ["manifest_bootstrap_status", "manifest_bootstrap_error"]


def _resync_app_manifest_from_repo(
    app: RegisteredApp,
    *,
    fetch: _FetchFn | None = None,
    ref: str = "",
) -> ResyncResult:
    """Re-fetch + reconcile ``app``'s manifest from its source repo.

    See module docstring for the full contract. Returns a
    :class:`ResyncResult` describing what happened; never raises
    (mutations / API callers translate this into the GraphQL
    envelope).
    """
    from astrolift_scm.providers import ProviderError

    fetch_fn: _FetchFn = fetch if fetch is not None else _default_fetch

    if not app.source_repo:
        return ResyncResult(
            status="fetch_failed",
            changes=ResyncChanges(),
            error="app has no source_repo configured",
        )

    connection = _pick_source_connection(app)
    if connection is None:
        return ResyncResult(
            status="fetch_failed",
            changes=ResyncChanges(),
            error=(
                "no active source connection found for this organization — "
                "reconnect the source host under Settings -> Source connections"
            ),
        )

    deploy_branch = ref or app.deploy_branch or app.default_branch or "main"
    manifest_path = app.manifest_path or "astrolift.toml"

    try:
        repo_text = fetch_fn(connection, app.source_repo, manifest_path, deploy_branch)
    except ProviderError as exc:
        return ResyncResult(
            status="fetch_failed",
            changes=ResyncChanges(),
            error=f"{exc.code}: {exc.message}",
        )
    except Exception as exc:  # noqa: BLE001 — surface anything as fetch_failed
        log.exception(
            "resync fetch crashed for app %s (repo=%s)",
            app.pk,
            app.source_repo,
        )
        return ResyncResult(
            status="fetch_failed",
            changes=ResyncChanges(),
            error=str(exc) or exc.__class__.__name__,
        )

    if repo_text is None:
        return ResyncResult(
            status="fetch_failed",
            changes=ResyncChanges(),
            error=(f"{manifest_path!r} not found on {deploy_branch!r} of {app.source_repo!r}"),
        )

    # Parse the repo content first. A repo with broken TOML is a
    # ``fetch_failed`` from the operator's perspective — we never
    # silently apply garbage and never clobber the DB.
    try:
        repo_raw_manifest = parse_raw(repo_text)
        repo_manifest = _normalize_text(repo_text)
    except ManifestError as exc:
        # An agent config-repo library (astrolift_version + [skills.*]/
        # [tools.*], no name/workloads) can never parse as an app manifest.
        # Give the operator the actionable path instead of a raw parse error
        # about a missing top-level name (#1172).
        if detect_toml_schema(repo_text) == "agent_config":
            return ResyncResult(
                status="fetch_failed",
                changes=ResyncChanges(),
                error=(
                    f"{manifest_path!r} on {deploy_branch!r} is an agent "
                    "config-repo schema (astrolift_version + [skills.*]/"
                    "[tools.*]), not an app manifest. Onboard it from Agents "
                    "-> Register agent repo, or import its skills from "
                    "Agents -> Import skills."
                ),
            )
        return ResyncResult(
            status="fetch_failed",
            changes=ResyncChanges(),
            error=f"repo manifest failed to parse: {exc}",
        )

    # Extract environment names so the mutation layer can bootstrap
    # AppEnvironment rows for apps that haven't been provisioned yet.
    env_names = list(repo_raw_manifest.raw.get("environments", {}).keys())

    repo_hash = manifest_hash(repo_manifest.serialized)
    db_raw = app.manifest_raw or ""

    # Try to compute the DB-side normalized snapshot. Tolerate a
    # missing / unparseable DB manifest by treating it as an empty
    # "before" — that's the freshly-registered-app case (#319).
    db_manifest: NormalizedManifest | None = None
    if db_raw.strip():
        try:
            db_manifest = _normalize_text(db_raw)
        except ManifestError:
            db_manifest = None

    # Diverged guard: a staged draft + repo content that differs
    # from the DB's source-of-truth means applying the repo would
    # drop work the operator hasn't pushed yet. Refuse rather than
    # clobber. The destructive ``syncManifestFromRepo`` mutation is
    # the explicit "yes, throw away my draft" path.
    staged = app.manifest_raw_staged or ""
    if staged.strip() and repo_text.strip() != db_raw.strip():
        return ResyncResult(
            status="diverged",
            changes=ResyncChanges(),
            error=(
                "DB has unpushed staged drafts that would be clobbered by "
                "repo content. Push or discard staged drafts before resync."
            ),
        )

    # No-op short-circuit. We compare on the normalized hash rather
    # than text so trivial formatting differences (trailing newlines,
    # comment-only edits) don't trigger a spurious "applied".
    if app.manifest_hash and app.manifest_hash == repo_hash:
        # Refresh the anchor + last_resync_at so the UI's relative
        # time updates even on a no-op click — operators expect that
        # signal as a heartbeat.
        with transaction.atomic():
            fields: list[str] = []
            if app.last_synced_hash != repo_hash:
                app.last_synced_hash = repo_hash
                fields.append("last_synced_hash")
            now = timezone.now()
            app.last_resync_at = now
            fields.append("last_resync_at")
            fields += _clear_stale_bootstrap_failure(app)
            fields += ["updated_at", "version"]
            app.save(update_fields=fields)
        return ResyncResult(status="in_sync", changes=ResyncChanges(), env_names=env_names)

    changes = _compute_changes(db_manifest, repo_manifest)

    # Apply. ``persist_manifest`` reconciles workloads + containers
    # and writes ``manifest_raw`` / ``manifest_hash`` /
    # ``manifest_normalized``. After it returns we anchor
    # ``last_synced_hash`` and stamp ``last_resync_at``.
    with transaction.atomic():
        persist_manifest(app, repo_manifest, raw_text=repo_text)
        app.last_synced_hash = repo_hash
        app.last_resync_at = timezone.now()
        # Sync from repo is destructive on the staging buffer when
        # repo == buffer (operator's pending draft already landed
        # upstream — clearing it is the right thing). We've already
        # refused above when the buffer would lose work.
        healed = _clear_stale_bootstrap_failure(app)
        if staged.strip() and repo_text.strip() == staged.strip():
            app.manifest_raw_staged = ""
            app.save(
                update_fields=[
                    "manifest_raw_staged",
                    "last_synced_hash",
                    "last_resync_at",
                    *healed,
                    "updated_at",
                    "version",
                ]
            )
        else:
            app.save(
                update_fields=[
                    "last_synced_hash",
                    "last_resync_at",
                    *healed,
                    "updated_at",
                    "version",
                ]
            )

    return ResyncResult(status="applied", changes=changes, env_names=env_names)


def summarize_changes(changes: ResyncChanges) -> str:
    """One-line, operator-friendly summary of a ResyncChanges.

    Used by the mutation payload's ``summary`` field so the UI's
    success toast doesn't need to reproduce the formatting in
    TypeScript.
    """
    if changes.is_empty:
        return "Already in sync."

    parts: list[str] = []
    added = len(changes.workloads_added)
    removed = len(changes.workloads_removed)
    changed = len(changes.workloads_changed)
    if added or removed or changed:
        bits: list[str] = []
        if added:
            bits.append(f"added {added} workload{'s' if added != 1 else ''}")
        if removed:
            bits.append(f"removed {removed}")
        if changed:
            bits.append(f"updated {changed}")
        parts.append(", ".join(bits))
    if changes.env_keys_changed:
        parts.append(
            f"{changes.env_keys_changed} env {'keys' if changes.env_keys_changed != 1 else 'key'} changed"
        )
    else:
        parts.append("env unchanged")
    msvc_added = len(changes.managed_services_added)
    msvc_removed = len(changes.managed_services_removed)
    if msvc_added or msvc_removed:
        bits = []
        if msvc_added:
            bits.append(f"added {msvc_added} managed service{'s' if msvc_added != 1 else ''}")
        if msvc_removed:
            bits.append(f"removed {msvc_removed}")
        parts.append(", ".join(bits))
    if changes.schedules_changed:
        parts.append(
            f"{changes.schedules_changed} schedule{'s' if changes.schedules_changed != 1 else ''} changed"
        )

    # Capitalize the first segment; period at the end.
    summary = ", ".join(parts)
    if summary:
        summary = summary[0].upper() + summary[1:]
    return summary + "."


# ---------------------------------------------------------------------------
# Monorepo agent discovery (spec 33, PR-3)
# ---------------------------------------------------------------------------
#
# Point at a repo and register each agent manifest it carries as its own
# agent ``Workload`` (under its own ``RegisteredApp`` keyed by
# ``(organization, source_repo, manifest_path)``). Two layouts:
#
#   * monorepo — ``agents/<slug>/astrolift.toml`` (N agents, N apps);
#   * single   — a root ``astrolift.toml`` (1 agent).
#
# The scan + parse live in ``astrolift_manifest.discover.scan_agent_manifests``
# (pure, fixture-testable); the repo tree comes from the SCM dispatcher's
# zipball (``astrolift_scm.providers.repo_tree.fetch_repo_tree``), reusing the
# existing ``fetch_zipball`` rather than adding a new git client. Both the
# tree fetch and the per-manifest persist mirror ``register_app`` /
# ``resync_app_manifest_from_repo`` so there is one registration contract.
#
# Idempotency + re-scan: every app is keyed by
# ``(organization, source_repo, manifest_path)``. Re-running the
# scan registers only manifests not already registered for the repo; existing
# agents are left untouched. A manifest that *disappeared* from the repo
# leaves its app + workload in place (we never hard-delete, and an operator
# may still want to dispatch or audit a removed agent); the per-manifest
# resync reconciles the bodies of agents that are still present.

# The tree fetcher returns ``{repo_relative_path: text}``; injectable so the
# discovery service can be exercised against a fixture repo tree without
# touching the SCM provider / network (mirrors ``_FetchFn`` above).
_TreeFn = Callable[[SourceConnection, str, str], "dict[str, str]"]


def _default_tree_fetch(
    connection: SourceConnection,
    repo_full_name: str,
    ref: str,
) -> dict[str, str]:
    """Production tree fetch: thin wrapper over the SCM provider's
    zipball-backed tree unpacker so the call site can be monkeypatched in
    tests without poking the import-time symbol on the provider package."""
    from astrolift_scm.providers.repo_tree import fetch_repo_tree

    return fetch_repo_tree(connection, repo_full_name=repo_full_name, ref=ref)


@dataclasses.dataclass(slots=True)
class DiscoveredAgent:
    """One agent manifest discovered in a repo, as a preview row.

    Carries the parsed identity the FE wizard's discovery step renders
    before the operator confirms registration, plus ``already_registered``
    so the wizard can disable / annotate manifests that point at an app
    already registered for this repo (the re-scan idempotency surfaced to
    the UI). ``manifest_path`` is the key that becomes
    ``RegisteredApp.manifest_path``.
    """

    manifest_path: str
    name: str
    slug: str
    workload_kind: str
    already_registered: bool


@dataclasses.dataclass(slots=True)
class DiscoverAgentsResult:
    """Return shape from :func:`discover_agent_manifests`.

    ``status`` is one of:

    - ``ok``           — the scan ran; ``agents`` lists every agent manifest
                          found (possibly empty when the repo has none).
    - ``fetch_failed`` — no usable source connection, or the SCM fetch
                          raised; ``error`` carries the host-side message.
    """

    status: str
    agents: list[DiscoveredAgent] = dataclasses.field(default_factory=list)
    error: str | None = None


@dataclasses.dataclass(slots=True)
class RegisteredAgent:
    """One agent app the register call created or matched.

    ``created`` is False when an app already existed for
    ``(source_repo, manifest_path)`` (idempotent re-run) — the workload is
    not re-persisted in that case beyond the body reconcile.

    ``skill_notes`` carries any **non-fatal** brief/skill-resolution warnings
    for this agent (a missing local skill path, a name absent from the
    built-in catalogue, an unavailable catalogue). The agent still registers
    when these are present — resolution failures degrade an agent's tooling,
    they do not block onboarding (spec 38 Phase 3 safety contract). Empty when
    everything resolved.
    """

    manifest_path: str
    slug: str
    app_guid: str
    workload_slug: str
    created: bool
    skill_notes: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(slots=True)
class RegisterAgentRepoResult:
    """Return shape from :func:`register_agent_repo`.

    ``status`` is one of ``ok`` / ``fetch_failed`` / ``no_agents`` /
    ``no_match`` / ``error``. ``no_match`` means a ``manifest_paths`` filter
    was supplied but none of the requested paths matched a discovered agent
    manifest. ``agents`` and ``workflows`` list what was reconciled (only on
    ``ok``); ``no_agents`` means neither an agent manifest nor a
    ``workflows/**/*.toml`` definition was present. ``error`` carries the
    message on failure statuses.
    """

    status: str
    agents: list[RegisteredAgent] = dataclasses.field(default_factory=list)
    workflows: list[Any] = dataclasses.field(default_factory=list)
    error: str | None = None
    skipped: list[str] = dataclasses.field(default_factory=list)
    """Near misses: manifests that declare an agent workload and were not
    kept, each already phrased as one operator-facing line (#1697)."""


def _scan_repo_for_agents(
    *,
    source_kind: str,
    source_repo: str,
    ref: str,
    organization_id: int,
    tree: _TreeFn | None,
) -> tuple[list[DiscoveredAgentManifest] | None, dict[str, str], str | None, list[str]]:
    """Fetch ``source_repo`` at ``ref`` and run the agent-manifest scan.

    Resolves a usable ``SourceConnection`` for ``(organization_id,
    source_kind)`` (same selection as the per-app resync), fetches the repo
    tree, and returns ``(discovered, files, None)`` on success or
    ``(None, {}, error_message)`` when there is no connection / the fetch
    fails. ``files`` is the fetched ``{path: contents}`` repo tree — returned
    so registration can resolve local skill / brief folders out of it (spec 38
    Phase 3) without re-fetching.
    """
    from astrolift_scm.providers import ProviderError

    tree_fn: _TreeFn = tree if tree is not None else _default_tree_fetch

    # Reuse the per-app connection picker by constructing a throwaway
    # RegisteredApp-shaped lookup: the picker only reads ``organization_id``
    # + ``source_kind``, so a lightweight unsaved instance is enough and
    # avoids duplicating the preference-ranking logic.
    probe = RegisteredApp(organization_id=organization_id, source_kind=source_kind)
    connection = _pick_source_connection(probe)

    files: dict[str, str] = {}
    conn_error: str | None = None
    if connection is not None:
        try:
            files = tree_fn(connection, source_repo, ref)
        except ProviderError as exc:
            conn_error = f"{exc.code}: {exc.message}"
        except Exception as exc:  # noqa: BLE001 — surface anything as fetch_failed
            log.exception("agent-repo scan fetch crashed (repo=%s)", source_repo)
            conn_error = str(exc) or exc.__class__.__name__

    skipped: list[str] = []
    try:
        if files:
            discovered, skips = scan_agent_manifests_with_skips(files)
            skipped = [f"{row.manifest_path}: {row.reason}" for row in skips]
        else:
            discovered = []
    except AgentFederationError as exc:
        return None, {}, str(exc), []

    # Fallback: when the per-org SourceConnection path yields no agent
    # manifests — no connection, a fetch error, or an empty/inaccessible tree
    # (e.g. a stale or under-scoped GitHub App install) — retry with the
    # install-wide GITHUB_PAT, the same credential dispatch-time brief
    # assembly uses. Only in the real fetch path (a test-injected ``tree``
    # opts out) and only for GitHub, so registration and dispatch read the
    # repo through the same working credential.
    if not discovered and tree is None and source_kind == "github":
        pat_files, pat_error = _pat_fallback_tree(source_repo, ref)
        if pat_files:
            try:
                pat_discovered, pat_skips = scan_agent_manifests_with_skips(pat_files)
            except AgentFederationError as exc:
                return None, {}, str(exc), []
            # Keep the fetched tree even when it contains only declarative
            # ``workflows/**/*.toml`` definitions.  The caller scans those
            # after this agent-specific discovery pass; discarding the tree
            # here made workflow-only config repos impossible through PAT
            # fallback.
            return (
                pat_discovered,
                pat_files,
                None,
                [f"{row.manifest_path}: {row.reason}" for row in pat_skips],
            )
        conn_error = conn_error or pat_error

    if not discovered:
        if connection is None and conn_error is None:
            conn_error = (
                "no active source connection found for this organization — "
                "reconnect the source host under Settings -> Source connections"
            )
        # A connection that fetched a tree with no agent manifests is not an
        # error — fall through and return the empty discovery so the caller
        # maps it to ``no_agents`` (unchanged). Only surface an error when the
        # fetch failed or there was nothing to fetch.
        if conn_error is not None:
            return None, {}, conn_error, []

    return discovered, files, None, skipped


def _pat_fallback_tree(source_repo: str, ref: str) -> tuple[dict[str, str], str | None]:
    """Best-effort ``settings.GITHUB_PAT`` repo-tree fetch for the register
    scan fallback. Returns ``(files, error)``: ``files`` is the
    ``{path: contents}`` tree (``{}`` when no PAT is configured or the fetch
    failed), ``error`` a message on failure (``None`` otherwise). Never
    raises — a fallback miss must not mask the primary connection outcome.
    """
    from astrolift_scm.providers.repo_tree import fetch_repo_tree_with_pat

    try:
        files = fetch_repo_tree_with_pat(repo_full_name=source_repo, ref=ref)
    except Exception as exc:  # noqa: BLE001 — fallback miss, not fatal
        log.warning("agent-repo PAT fallback fetch failed (repo=%s): %s", source_repo, exc)
        return {}, str(exc) or exc.__class__.__name__
    return (files or {}), None


def discover_agent_manifests(
    *,
    organization_id: int,
    source_kind: str,
    source_repo: str,
    ref: str,
    tree: _TreeFn | None = None,
) -> DiscoverAgentsResult:
    """Scan ``source_repo`` for agent manifests and return a preview.

    Does NOT persist anything — backs the FE wizard's discovery step. Each
    returned row carries the parsed name/slug/kind plus
    ``already_registered`` (True when an app for that
    ``(source_repo, manifest_path)`` already exists, soft-deleted excluded),
    so the wizard can show which agents are new vs already onboarded.
    """
    discovered, _files, error, _skipped = _scan_repo_for_agents(
        source_kind=source_kind,
        source_repo=source_repo,
        ref=ref,
        organization_id=organization_id,
        tree=tree,
    )
    if discovered is None:
        return DiscoverAgentsResult(status="fetch_failed", error=error)

    existing_paths = set(
        RegisteredApp.objects.filter(
            organization_id=organization_id,
            source_repo=source_repo,
            deleted_at__isnull=True,
        ).values_list("manifest_path", flat=True)
    )

    rows = [
        DiscoveredAgent(
            manifest_path=d.manifest_path,
            name=d.name,
            slug=d.slug,
            workload_kind=d.workload_kind,
            already_registered=d.manifest_path in existing_paths,
        )
        for d in discovered
    ]
    return DiscoverAgentsResult(status="ok", agents=rows)


def _register_one_agent(
    *,
    project,
    discovered: DiscoveredAgentManifest,
    source_kind: str,
    source_repo: str,
    source_url: str,
    default_branch: str,
    deploy_branch: str,
    default_cluster,
    agent_repo_tree: dict[str, str],
    catalogue_tree: dict[str, str] | None,
    org_repo_tree_cache: dict[str, dict[str, str] | None],
    source_archive: bytes | None,
) -> RegisteredAgent:
    """Create (or match) one agent app + workload for a discovered manifest.

    Idempotent on ``(source_repo, manifest_path)``: when an app already
    exists for the pair the existing app is reused (and its agent workload's
    body reconciled from the repo via ``persist_manifest``), so a re-scan
    adds only genuinely-new agents. Mirrors ``register_app``'s field
    defaults; binds ``default_tenant_cluster`` best-effort (agents are
    dispatched on demand — the dispatch path enforces the managed-cluster
    requirement, so registration does not reject when none exists yet).

    After the workload is persisted, the manifest's ``brief`` / ``skills``
    pointers (spec 38) are resolved + stored: each skill is upserted as an
    org-scoped ``Skill``, the workload's ``AgentSkillRef`` set is reconciled,
    and (when a brief is declared) a ``Brief`` is assembled and linked via
    ``Workload.brief``. Resolution failures are non-fatal — they are returned
    as ``skill_notes`` on the result, not raised, so one bad skill path or a
    catalogue outage degrades an agent's tooling rather than aborting the
    whole registration pass.
    """
    org = project.organization
    raw_manifest = parse_raw(discovered.raw_text)
    manifest = _normalize_text(discovered.raw_text)

    app = RegisteredApp.objects.filter(
        organization=org,
        source_repo=source_repo,
        manifest_path=discovered.manifest_path,
        deleted_at__isnull=True,
    ).first()
    created = app is None
    if app is None:
        from astrolift_registry.models import Workload

        if Workload.objects.filter(
            slug=discovered.slug,
            kind=Workload.Kind.AGENT,
            registered_app__organization=org,
            registered_app__deleted_at__isnull=True,
            deleted_at__isnull=True,
        ).exists():
            raise ValueError(
                f"agent workload slug {discovered.slug!r} is already registered in this organization"
            )
        # Slug must be unique per org; an agent's manifest name can repeat
        # across repos, so qualify with the manifest path's agent segment
        # when it isn't the root manifest. ``_agent_app_slug`` resolves a
        # collision deterministically.
        app_slug = _agent_app_slug(org, discovered)
        app = RegisteredApp.objects.create(
            organization=org,
            team=project.team,
            project=project,
            name=discovered.name,
            slug=app_slug,
            source_kind=source_kind,
            source_repo=source_repo,
            source_url=source_url,
            manifest_path=discovered.manifest_path,
            manifest_raw=discovered.raw_text,
            default_branch=default_branch,
            deploy_branch=deploy_branch,
            k8s_namespace=app_namespace(
                organization_slug=org.slug,
                app_slug=app_slug,
            ),
            subdomain=app_slug,
            default_tenant_cluster=default_cluster,
        )

    # The home-team FK is a compatibility pointer; AppTeamAccess is the
    # canonical authorization relation. Keep every registration path at the
    # same invariant as move/grant flows, including pre-existing rows created
    # before that relation was introduced.
    from astrolift_registry.schema.mutations.helpers import _ensure_owner_access

    _ensure_owner_access(app, app.team_id)

    # Reconcile the agent workload (+ container) rows from the manifest.
    # On a fresh app this creates them; on a re-matched app it updates only
    # what changed (and leaves the row otherwise — never hard-deleted).
    persist_manifest(app, manifest, raw_text=discovered.raw_text)
    app.last_resync_at = timezone.now()
    app.save(update_fields=["last_resync_at", "updated_at", "version"])

    # Keep the dispatch environment recipe anchored to the same source slice
    # as the registered workload. This is the bridge that makes manual,
    # webhook, cron, loop, and workflow dispatches resolve identical env and
    # secret bindings without hand-created production rows.
    _upsert_agent_environment_spec(
        app=app,
        workload_slug=discovered.slug,
        raw_manifest=raw_manifest,
        source_repo=source_repo,
        deploy_branch=deploy_branch,
        manifest_path=discovered.manifest_path,
    )

    # Resolve + store the agent's brief + skills (spec 38 Phase 3). The
    # discovered slug is the agent workload's name == its slug; fetch the
    # persisted Workload row to attach the AgentSkillRef / Brief to.
    skill_notes = _resolve_agent_brief_and_skills(
        app=app,
        workload_slug=discovered.slug,
        raw_manifest=raw_manifest,
        manifest_path=discovered.manifest_path,
        agent_repo_tree=agent_repo_tree,
        catalogue_tree=catalogue_tree,
        org_repo_tree_cache=org_repo_tree_cache,
        source_archive=source_archive,
        federation=discovered.federation,
    )

    return RegisteredAgent(
        manifest_path=discovered.manifest_path,
        slug=discovered.slug,
        app_guid=str(app.guid),
        workload_slug=discovered.slug,
        created=created,
        skill_notes=skill_notes,
    )


def _upsert_agent_environment_spec(
    *,
    app: RegisteredApp,
    workload_slug: str,
    raw_manifest,
    source_repo: str,
    deploy_branch: str,
    manifest_path: str,
) -> None:
    """Create/update the source-owned portion of an agent environment spec.

    Runtime toggles controlled by operators (VNC and managed model) are left
    untouched on update. Manifest-owned environment values and secret
    bindings reconcile from source, while secret *values* remain exclusively
    in the external secrets backend.
    """
    from astrolift_agents.models import AgentEnvironmentSpec
    from astrolift_agents.services.agent_package import project_manifest_environment

    workload = app.workloads.filter(
        slug=workload_slug,
        kind="agent",
        deleted_at__isnull=True,
    ).first()
    if workload is None:
        return
    primary = workload.containers.filter(is_primary=True).first()
    image = getattr(primary, "image_ref", "") or ""
    agent_type = (
        AgentEnvironmentSpec.AgentType.CODEX
        if "codex" in image.lower()
        else AgentEnvironmentSpec.AgentType.CLAUDE
    )

    env_section = raw_manifest.raw.get("environment") or {}
    env_vars, secret_refs = project_manifest_environment(
        env_section,
        raw_manifest.raw.get("secrets"),
    )

    spec = AgentEnvironmentSpec.objects.filter(
        organization=app.organization,
        slug=workload_slug,
        deleted_at__isnull=True,
    ).first()
    if spec is None:
        spec = AgentEnvironmentSpec(
            organization=app.organization,
            slug=workload_slug,
            name=f"{workload.name} runtime",
            agent_type=agent_type,
        )
    spec.name = spec.name or f"{workload.name} runtime"
    spec.agent_type = agent_type
    spec.image_tag = image
    spec.tool_preset = str(env_section.get("tool_preset") or "")[:128]
    spec.allow_install = bool(env_section.get("allow_install", False))
    spec.env_vars = env_vars
    spec.secret_refs = secret_refs
    spec.config_repo = source_repo
    spec.config_branch = deploy_branch or "main"
    spec.config_manifest_path = manifest_path
    spec.save()


def _resolve_agent_brief_and_skills(
    *,
    app: RegisteredApp,
    workload_slug: str,
    raw_manifest,
    manifest_path: str,
    agent_repo_tree: dict[str, str],
    catalogue_tree: dict[str, str] | None,
    org_repo_tree_cache: dict[str, dict[str, str] | None],
    source_archive: bytes | None,
    federation: dict | None = None,
) -> list[str]:
    """Resolve + persist the agent workload's brief + skills; return notes.

    A self-contained image with neither prose brief nor skills still receives
    a minimal canonical Agent Package. Resolution failures are non-fatal: the
    per-skill / per-brief notes are returned for the caller to surface, and an
    unexpected error is caught and logged so a resolution bug cannot abort the
    whole registration transaction (the agent workload is already persisted).
    """
    from astrolift_agents.services.agent_skill_registration import (
        resolve_and_store_agent_skills,
    )

    workload = app.workloads.filter(
        slug=workload_slug,
        kind="agent",
        deleted_at__isnull=True,
    ).first()
    if workload is None:
        # persist_manifest just created/updated it, so this is unexpected —
        # log + a note rather than raise (registration stays non-fatal).
        log.warning(
            "agent workload %r not found after persist for app %s; skipping skill resolution",
            workload_slug,
            app.guid,
        )
        return [f"workload {workload_slug!r}: not found after persist; skills/brief not resolved"]

    from astrolift_agents.services.agent_package import AgentPackageError
    from astrolift_agents.services.agent_payload import AgentPayloadError

    try:
        return resolve_and_store_agent_skills(
            workload=workload,
            manifest=raw_manifest,
            manifest_path=manifest_path,
            agent_repo_tree=agent_repo_tree,
            catalogue_tree=catalogue_tree,
            org_repo_tree_cache=org_repo_tree_cache,
            source_archive=source_archive,
            federation=federation,
        )
    except (AgentPackageError, AgentPayloadError):
        # Invalid package boundaries and payload selection are source errors,
        # not optional skill degradation.  Let the surrounding transaction
        # roll back so registration cannot report success for an unrunnable
        # or unsafe package.
        raise
    except Exception as exc:  # noqa: BLE001 — unexpected optional resolution failure
        log.exception(
            "agent skill/brief resolution crashed for workload %r (app %s)",
            workload_slug,
            app.guid,
        )
        return [f"skill/brief resolution error: {exc or exc.__class__.__name__}"]


def _agent_app_slug(org, discovered: DiscoveredAgentManifest) -> str:
    """Pick a unique-per-org app slug for a discovered agent.

    Prefers the agent's own slug (the workload name); when an active app in
    the org already holds it, falls back to ``<agent-slug>-<dir>`` using the
    manifest's parent directory (e.g. ``agents/triage/astrolift.toml`` →
    ``triage``), then appends a numeric suffix as a last resort. Keeps slugs
    stable across re-scans because the same manifest path yields the same
    candidate sequence.
    """
    base = discovered.slug
    if not _slug_taken(org, base):
        return base

    # Directory-qualified candidate from the manifest path.
    segments = discovered.manifest_path.split("/")
    if len(segments) == 3:  # agents/<dir>/astrolift.toml
        qualified = f"{base}-{segments[1]}" if segments[1] != base else base
        if qualified != base and not _slug_taken(org, qualified):
            return qualified

    # Numeric suffix fallback.
    i = 2
    while _slug_taken(org, f"{base}-{i}"):
        i += 1
    return f"{base}-{i}"


def _slug_taken(org, slug: str) -> bool:
    return RegisteredApp.objects.filter(
        organization=org,
        slug=slug,
        deleted_at__isnull=True,
    ).exists()


def _archive_from_text_tree(files: dict[str, str]) -> bytes:
    """Build a host-shaped source ZIP for injected discovery fixtures."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, contents in sorted(files.items()):
            archive.writestr(f"repo-snapshot/{path}", contents.encode("utf-8"))
    return output.getvalue()


def _registration_source_archive(
    *,
    organization_id: int,
    source_kind: str,
    source_repo: str,
    ref: str,
    files: dict[str, str],
    injected_tree: bool,
) -> tuple[bytes | None, str | None]:
    """Fetch the byte-preserving archive bound to modular package Briefs.

    Discovery intentionally keeps only small UTF-8 files, which is enough to
    find manifests but cannot deliver binaries.  The package snapshot therefore
    fetches the original ZIP once per registration pass. Tests with an injected
    tree receive an equivalent synthetic archive.
    """
    if injected_tree:
        return _archive_from_text_tree(files), None

    from astrolift_scm.providers import ProviderError, fetch_zipball

    probe = RegisteredApp(organization_id=organization_id, source_kind=source_kind)
    connection = _pick_source_connection(probe)
    primary_error = ""
    if connection is not None:
        try:
            return fetch_zipball(connection, repo_full_name=source_repo, ref=ref), None
        except ProviderError as exc:
            primary_error = f"{exc.code}: {exc.message}"
        except Exception as exc:  # noqa: BLE001 - translated to registration envelope
            primary_error = str(exc) or exc.__class__.__name__

    if source_kind == "github":
        try:
            from astrolift_agents.services.brief_assembler import _fetch_zipball

            return _fetch_zipball(source_repo, ref), None
        except Exception as exc:  # noqa: BLE001 - translated to registration envelope
            fallback_error = str(exc) or exc.__class__.__name__
            return None, primary_error or fallback_error
    return None, primary_error or "no active source connection can fetch this package archive"


def _needs_package_archive(discovered: list[DiscoveredAgentManifest]) -> bool:
    for item in discovered:
        try:
            raw = parse_raw(item.raw_text).raw
        except ManifestError:
            continue
        if "package" in raw:
            return True
    return False


def _archive_matches_discovery(
    source_archive: bytes,
    discovered: list[DiscoveredAgentManifest],
) -> bool:
    """Confirm package manifests came from the same tree as the archive.

    Discovery and byte-preserving archive capture are two SCM reads. A push
    between them must not produce a Brief whose parsed manifest is from one
    commit while its scripts/binaries are from another. Refuse that mixed
    snapshot and let the caller retry the registration against one revision.
    """
    from astrolift_scm.providers.repo_tree import repo_tree_from_zipball_bytes

    try:
        archive_tree = repo_tree_from_zipball_bytes(source_archive)
    except (OSError, ValueError, zipfile.BadZipFile):
        return False
    return all(archive_tree.get(item.manifest_path) == item.raw_text for item in discovered)


def register_agent_repo(
    *,
    project,
    source_kind: str,
    source_repo: str,
    ref: str,
    source_url: str = "",
    default_branch: str = "main",
    deploy_branch: str = "",
    default_cluster=None,
    manifest_paths: list[str] | None = None,
    tree: _TreeFn | None = None,
) -> RegisterAgentRepoResult:
    """Register every agent manifest in ``source_repo`` under ``project``.

    Scans the repo (monorepo ``agents/*/astrolift.toml`` + root manifest),
    and for each agent manifest creates an agent ``Workload`` under its own
    ``RegisteredApp``. Idempotent on ``(source_repo, manifest_path)`` so a
    re-run adds only manifests not already registered. Every row is created
    under ``project`` (its organization is the tenancy boundary).

    ``manifest_paths`` optionally restricts the pass to a subset of the
    discovered manifests (#933): when given, only manifests at those paths
    are registered (the wizard's checked agents); requested paths that don't
    match a discovered manifest are ignored. When omitted/empty, every
    discovered agent manifest is registered.

    Returns ``no_agents`` when the repo has neither agent nor workflow manifests, ``no_match``
    when a ``manifest_paths`` filter excluded every discovered manifest,
    ``fetch_failed`` when the repo can't be fetched, ``error`` on an
    unexpected persist failure, else ``ok`` with the per-manifest outcome.
    """
    discovered, files, error, skipped = _scan_repo_for_agents(
        source_kind=source_kind,
        source_repo=source_repo,
        ref=ref,
        organization_id=project.organization_id,
        tree=tree,
    )
    if discovered is None:
        return RegisterAgentRepoResult(status="fetch_failed", error=error)
    from workflows.repo_sync import (
        discover_repository_workflows,
        reconcile_repository_workflows,
    )

    try:
        workflow_manifests = discover_repository_workflows(files)
    except ManifestError as exc:
        return RegisterAgentRepoResult(status="error", error=str(exc))
    if not discovered and not workflow_manifests:
        # Carry the near misses. "No agent manifests found" is true and
        # useless when the operator's agent *was* seen and skipped for a
        # reason they can act on (#1697).
        return RegisterAgentRepoResult(status="no_agents", skipped=skipped)

    # Subset registration (#933): keep only the requested manifests. Unknown
    # paths are silently dropped (a stale wizard selection shouldn't fail the
    # whole pass); when the filter leaves nothing, surface a clear no_match so
    # the caller knows their selection matched no discovered agent.
    if manifest_paths and discovered:
        requested = set(manifest_paths)
        selected = [d for d in discovered if d.manifest_path in requested]
        if not selected:
            available = ", ".join(sorted(d.manifest_path for d in discovered))
            return RegisterAgentRepoResult(
                status="no_match",
                error=(
                    "none of the requested manifestPaths matched a discovered "
                    f"agent manifest (available: {available})"
                ),
            )
        discovered = selected

    eff_deploy_branch = deploy_branch or default_branch or "main"

    source_archive: bytes | None = None
    if discovered and _needs_package_archive(discovered):
        source_archive, archive_error = _registration_source_archive(
            organization_id=project.organization_id,
            source_kind=source_kind,
            source_repo=source_repo,
            ref=ref,
            files=files,
            injected_tree=tree is not None,
        )
        if source_archive is None:
            return RegisterAgentRepoResult(
                status="fetch_failed",
                error=f"could not snapshot modular agent package: {archive_error or 'unknown fetch error'}",
            )
        if not _archive_matches_discovery(source_archive, discovered):
            return RegisterAgentRepoResult(
                status="fetch_failed",
                error=(
                    "source changed while the agent package was being snapshotted; "
                    "retry the sync against a stable branch or commit SHA"
                ),
            )

    # Fetch the built-in skills catalogue ONCE for this registration pass and
    # thread it into every agent's resolution (spec 39c). Done only when some
    # discovered manifest actually references a named (non-local) skill — a
    # repo of purely-local-skill agents never touches the network. A fetch
    # failure is non-fatal: ``catalogue_tree`` stays None and every named
    # skill records a note while local skills + the agents still register.
    catalogue_tree = _load_catalogue_if_needed(discovered) if discovered else None

    # Per-pass cache of fetched org skill-repo trees (spec 39d), keyed by
    # ``"<alias>@<ref>"`` so a repo referenced by many agents / skills across
    # this registration pass is fetched once. Threaded into every agent's
    # resolution. A cached ``None`` records a fetch failure for that alias@ref
    # so it isn't re-tried this pass.
    org_repo_tree_cache: dict[str, dict[str, str] | None] = {}

    try:
        with transaction.atomic():
            agents = [
                _register_one_agent(
                    project=project,
                    discovered=d,
                    source_kind=source_kind,
                    source_repo=source_repo,
                    source_url=source_url,
                    default_branch=default_branch or "main",
                    deploy_branch=eff_deploy_branch,
                    default_cluster=default_cluster,
                    agent_repo_tree=files,
                    catalogue_tree=catalogue_tree,
                    org_repo_tree_cache=org_repo_tree_cache,
                    source_archive=source_archive,
                )
                for d in discovered
            ]
            workflows = reconcile_repository_workflows(
                organization=project.organization,
                project=project,
                source_repo=source_repo,
                source_ref=ref,
                manifests=workflow_manifests,
            )
    except Exception as exc:  # noqa: BLE001 — surface as a clean envelope
        log.exception("agent-repo registration failed (repo=%s)", source_repo)
        return RegisterAgentRepoResult(status="error", error=str(exc) or exc.__class__.__name__)

    return RegisterAgentRepoResult(status="ok", agents=agents, workflows=workflows)


def _load_catalogue_if_needed(
    discovered: list[DiscoveredAgentManifest],
) -> dict[str, str] | None:
    """Fetch the built-in catalogue tree iff some manifest uses a catalogue skill.

    A *catalogue* skill (a bare string with no ``/``) resolves from the
    built-in catalogue; *local* skills (``{name = "path"}`` tables / ``./``
    strings) and *org-repo* skills (``<alias>/<path>@<ref>`` strings, spec
    39d) do not. Parse each discovered manifest's skills and fetch the
    catalogue once only when at least one catalogue skill is present, so a
    repo of purely-local / org-repo agents never hits the catalogue. Returns
    the ``{path: text}`` catalogue map, or ``None`` when no catalogue skill is
    referenced OR the fetch failed (a failed fetch is non-fatal — every
    catalogue skill then records a note downstream).
    """
    needs_catalogue = False
    for d in discovered:
        try:
            rm = parse_raw(d.raw_text)
        except ManifestError:
            # A manifest that no longer parses was already skipped by the
            # scanner for registration; ignore it for catalogue need too.
            continue
        # Only a *catalogue* ref needs the built-in catalogue tree. Local
        # skills read from the agent repo; org-repo skills (spec 39d) fetch
        # their own registered repo — neither touches the catalogue.
        if any(s.kind == "catalogue" for s in rm.skills):
            needs_catalogue = True
            break
    if not needs_catalogue:
        return None

    from astrolift_agents.services.skill_resolver import (
        CatalogueFetchError,
        load_catalogue_tree,
    )

    try:
        return load_catalogue_tree()
    except CatalogueFetchError:
        # Already logged once in load_catalogue_tree; downstream records a
        # per-skill note for every named skill so the operator sees it.
        return None


def resync_agent_repo_manifests(
    *,
    project,
    source_repo: str,
    source_kind: str = "",
    ref: str = "",
    default_cluster=None,
    tree: _TreeFn | None = None,
) -> RegisterAgentRepoResult:
    """Re-scan ``source_repo`` and add agents that appeared since last scan.

    The keep-the-list-in-sync counterpart of :func:`register_agent_repo`:
    pointed at a repo whose agents were already registered, it picks up
    newly-added ``agents/<slug>/astrolift.toml`` manifests and registers
    them WITHOUT duplicating the existing agents (idempotent on
    ``(source_repo, manifest_path)``), and reconciles the bodies of agents
    that are still present.

    Disappeared manifests: an agent whose manifest was *removed* from the
    repo is intentionally LEFT in place (app + workload not deleted). We
    never hard-delete onboarding data — a removed agent may still need to be
    dispatched or audited, and a soft-delete-on-disappear policy is a
    deliberate, separately-scoped decision (it would also race a transient
    fetch that returned a partial tree). This function only ever ADDS /
    UPDATES; pruning is out of scope by design.

    ``source_kind`` / ``ref`` default to the values on an existing app for
    the repo when omitted, so a caller that only has the repo handle still
    resolves the right connection + branch.
    """
    anchor = (
        RegisteredApp.objects.filter(
            organization_id=project.organization_id,
            source_repo=source_repo,
            deleted_at__isnull=True,
        )
        .order_by("pk")
        .first()
    )
    eff_source_kind = source_kind or (anchor.source_kind if anchor else "github")
    eff_ref = ref or (anchor.deploy_branch or anchor.default_branch if anchor else "") or "main"
    eff_source_url = anchor.source_url if anchor else ""
    eff_default_branch = (anchor.default_branch if anchor else "main") or "main"
    eff_deploy_branch = (anchor.deploy_branch if anchor else "") or eff_default_branch

    return register_agent_repo(
        project=project,
        source_kind=eff_source_kind,
        source_repo=source_repo,
        ref=eff_ref,
        source_url=eff_source_url,
        default_branch=eff_default_branch,
        deploy_branch=eff_deploy_branch,
        default_cluster=default_cluster,
        tree=tree,
    )


# ---------------------------------------------------------------------------
# Monorepo / multi-service app discovery (#979)
# ---------------------------------------------------------------------------
#
# The app-side mirror of the agent monorepo path above: point at a repo and
# register each *app* manifest it carries (one ``apps/<slug>/astrolift.toml``
# per service, plus a root ``astrolift.toml``) as its own ``RegisteredApp``
# keyed by ``(organization, source_repo, manifest_path)``. The scan + the app/agent split
# live in ``astrolift_manifest.discover.scan_app_manifests`` (pure,
# fixture-testable); the repo tree comes from the same SCM zipball fetch the
# agent path uses (``_scan_repo_for_apps`` reuses ``_default_tree_fetch``).
#
# Each created app's ``build_context`` is set to the manifest's own directory
# (``apps/<slug>``) so each service builds from its own subdir — the build
# activity reads ``RegisteredApp.build_context`` (build_image.py is UNCHANGED).
#
# Idempotency + re-scan mirror the agent path: every app is keyed by
# ``(organization, source_repo, manifest_path)``. Re-running registers only
# manifests not already registered for
# the repo; an app whose manifest disappeared is left in place (never
# hard-deleted), and the per-manifest body is reconciled via ``persist_manifest``.


@dataclasses.dataclass(slots=True)
class DiscoveredAppPreview:
    """One app manifest discovered in a repo, as a preview row.

    Carries the parsed identity the FE wizard's discovery step renders before
    the operator confirms registration, plus ``already_registered`` so the
    wizard can disable / annotate manifests that point at an app already
    registered for this repo. ``manifest_path`` is the key that becomes
    ``RegisteredApp.manifest_path``; ``build_context`` is the per-service
    subdir the app builds from.
    """

    manifest_path: str
    name: str
    build_context: str
    workload_count: int
    already_registered: bool


@dataclasses.dataclass(slots=True)
class DiscoverAppsResult:
    """Return shape from :func:`discover_app_manifests`.

    ``status`` is ``ok`` (the scan ran; ``apps`` lists every app manifest
    found, possibly empty) or ``fetch_failed`` (no usable source connection /
    the SCM fetch raised; ``error`` carries the host-side message).
    """

    status: str
    apps: list[DiscoveredAppPreview] = dataclasses.field(default_factory=list)
    error: str | None = None


@dataclasses.dataclass(slots=True)
class RegisteredAppEntry:
    """One app the register call created or matched.

    ``created`` is False when an app already existed for
    ``(source_repo, manifest_path)`` (idempotent re-run) — the workloads are
    reconciled from the manifest body but the app row is reused.
    """

    manifest_path: str
    slug: str
    app_guid: str
    build_context: str
    created: bool


@dataclasses.dataclass(slots=True)
class RegisterAppRepoResult:
    """Return shape from :func:`register_app_repo`.

    ``status`` is one of ``ok`` / ``fetch_failed`` / ``no_apps`` / ``error``.
    ``apps`` lists what was created or matched (only on ``ok``); ``error``
    carries the message on the failure statuses.
    """

    status: str
    apps: list[RegisteredAppEntry] = dataclasses.field(default_factory=list)
    error: str | None = None


def _scan_repo_for_apps(
    *,
    source_kind: str,
    source_repo: str,
    ref: str,
    organization_id: int,
    tree: _TreeFn | None,
) -> tuple[list[DiscoveredAppManifest] | None, str | None]:
    """Fetch ``source_repo`` at ``ref`` and run the app-manifest scan.

    Resolves a usable ``SourceConnection`` for ``(organization_id,
    source_kind)`` (same selection as the per-app resync), fetches the repo
    tree, and returns ``(discovered, None)`` on success or
    ``(None, error_message)`` when there is no connection / the fetch fails.
    """
    from astrolift_scm.providers import ProviderError

    tree_fn: _TreeFn = tree if tree is not None else _default_tree_fetch

    probe = RegisteredApp(organization_id=organization_id, source_kind=source_kind)
    connection = _pick_source_connection(probe)
    if connection is None:
        return (
            None,
            (
                "no active source connection found for this organization — "
                "reconnect the source host under Settings -> Source connections"
            ),
        )

    try:
        files = tree_fn(connection, source_repo, ref)
    except ProviderError as exc:
        return None, f"{exc.code}: {exc.message}"
    except Exception as exc:  # noqa: BLE001 — surface anything as fetch_failed
        log.exception("app-repo scan fetch crashed (repo=%s)", source_repo)
        return None, str(exc) or exc.__class__.__name__

    return scan_app_manifests(files), None


def discover_app_manifests(
    *,
    organization_id: int,
    source_kind: str,
    source_repo: str,
    ref: str,
    tree: _TreeFn | None = None,
) -> DiscoverAppsResult:
    """Scan ``source_repo`` for app manifests and return a preview.

    Does NOT persist anything — backs the FE wizard's discovery step. Each
    returned row carries the parsed name / build_context / workload_count plus
    ``already_registered`` (True when an app for that
    ``(source_repo, manifest_path)`` already exists, soft-deleted excluded).
    """
    discovered, error = _scan_repo_for_apps(
        source_kind=source_kind,
        source_repo=source_repo,
        ref=ref,
        organization_id=organization_id,
        tree=tree,
    )
    if discovered is None:
        return DiscoverAppsResult(status="fetch_failed", error=error)

    existing_paths = set(
        RegisteredApp.objects.filter(
            organization_id=organization_id,
            source_repo=source_repo,
            deleted_at__isnull=True,
        ).values_list("manifest_path", flat=True)
    )

    rows = [
        DiscoveredAppPreview(
            manifest_path=d.manifest_path,
            name=d.name,
            build_context=d.build_context,
            workload_count=d.workload_count,
            already_registered=d.manifest_path in existing_paths,
        )
        for d in discovered
    ]
    return DiscoverAppsResult(status="ok", apps=rows)


def _app_repo_slug(org, discovered: DiscoveredAppManifest) -> str:
    """Pick a unique-per-org app slug for a discovered app manifest.

    Prefers the manifest's own directory segment for a monorepo service
    (``apps/web/astrolift.toml`` → ``web``) and the slugified manifest name for
    a root manifest. On a collision with an active app in the org, appends a
    numeric suffix. Keeps slugs stable across re-scans because the same
    manifest path yields the same candidate sequence.
    """
    from django.utils.text import slugify

    segments = discovered.manifest_path.split("/")
    if len(segments) == 3:  # apps/<dir>/astrolift.toml
        base = slugify(segments[1])
    else:
        base = slugify(discovered.name)
    if not base:
        base = "app"
    if not _slug_taken(org, base):
        return base

    i = 2
    while _slug_taken(org, f"{base}-{i}"):
        i += 1
    return f"{base}-{i}"


def _register_one_app(
    *,
    project,
    discovered: DiscoveredAppManifest,
    source_kind: str,
    source_repo: str,
    source_url: str,
    default_branch: str,
    deploy_branch: str,
    default_cluster,
) -> RegisteredAppEntry:
    """Create (or match) one ``RegisteredApp`` + its workloads for a manifest.

    Idempotent on ``(source_repo, manifest_path)``: when an app already exists
    for the pair the existing app is reused (and its workloads reconciled from
    the repo via ``persist_manifest``), so a re-scan adds only genuinely-new
    apps. Mirrors ``register_app``'s field defaults and sets ``build_context``
    to the manifest's own subdir so each service builds from its own folder.
    """
    org = project.organization
    manifest = _normalize_text(discovered.raw_text)

    app = RegisteredApp.objects.filter(
        organization=org,
        source_repo=source_repo,
        manifest_path=discovered.manifest_path,
        deleted_at__isnull=True,
    ).first()
    created = app is None
    if app is None:
        app_slug = _app_repo_slug(org, discovered)
        app = RegisteredApp.objects.create(
            organization=org,
            team=project.team,
            project=project,
            name=discovered.name,
            slug=app_slug,
            source_kind=source_kind,
            source_repo=source_repo,
            source_url=source_url,
            manifest_path=discovered.manifest_path,
            manifest_raw=discovered.raw_text,
            default_branch=default_branch,
            deploy_branch=deploy_branch,
            # Each service builds from its own subdir (build_image.py reads
            # RegisteredApp.build_context as the context path in the source tree).
            build_context=discovered.build_context,
            k8s_namespace=app_namespace(
                organization_slug=org.slug,
                app_slug=app_slug,
            ),
            subdomain=app_slug,
            default_tenant_cluster=default_cluster,
        )

    from astrolift_registry.schema.mutations.helpers import _ensure_owner_access

    _ensure_owner_access(app, app.team_id)

    # Reconcile the workload (+ container) rows from the manifest. On a fresh
    # app this creates them; on a re-matched app it updates only what changed.
    persist_manifest(app, manifest, raw_text=discovered.raw_text)

    return RegisteredAppEntry(
        manifest_path=discovered.manifest_path,
        slug=app.slug,
        app_guid=str(app.guid),
        build_context=app.build_context,
        created=created,
    )


def register_app_repo(
    *,
    project,
    source_kind: str,
    source_repo: str,
    ref: str,
    source_url: str = "",
    default_branch: str = "main",
    deploy_branch: str = "",
    default_cluster=None,
    tree: _TreeFn | None = None,
) -> RegisterAppRepoResult:
    """Register every app manifest in ``source_repo`` under ``project``.

    Scans the repo (monorepo ``apps/*/astrolift.toml`` + root
    ``astrolift.toml``) and creates one ``RegisteredApp`` (with its workloads)
    per discovered service, each building from its own subdir. Idempotent on
    ``(source_repo, manifest_path)`` so a re-run adds only manifests not
    already registered. Every row is created under ``project`` (its
    organization is the tenancy boundary).

    Returns ``no_apps`` when the repo has no app manifests, ``fetch_failed``
    when the repo can't be fetched, ``error`` on an unexpected persist
    failure, else ``ok`` with the per-manifest outcome.
    """
    discovered, error = _scan_repo_for_apps(
        source_kind=source_kind,
        source_repo=source_repo,
        ref=ref,
        organization_id=project.organization_id,
        tree=tree,
    )
    if discovered is None:
        return RegisterAppRepoResult(status="fetch_failed", error=error)
    if not discovered:
        return RegisterAppRepoResult(status="no_apps")

    eff_deploy_branch = deploy_branch or default_branch or "main"

    try:
        with transaction.atomic():
            apps = [
                _register_one_app(
                    project=project,
                    discovered=d,
                    source_kind=source_kind,
                    source_repo=source_repo,
                    source_url=source_url,
                    default_branch=default_branch or "main",
                    deploy_branch=eff_deploy_branch,
                    default_cluster=default_cluster,
                )
                for d in discovered
            ]
    except Exception as exc:  # noqa: BLE001 — surface as a clean envelope
        log.exception("app-repo registration failed (repo=%s)", source_repo)
        return RegisterAppRepoResult(status="error", error=str(exc) or exc.__class__.__name__)

    return RegisterAppRepoResult(status="ok", apps=apps)
