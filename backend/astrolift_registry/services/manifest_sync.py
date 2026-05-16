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
import logging
from collections.abc import Callable

from django.db import transaction
from django.utils import timezone

from astrolift_manifest.normalize import NormalizationDefaults, manifest_hash, normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_manifest.types import (
    ManagedServiceManifest,
    NormalizedManifest,
    WorkloadManifest,
)
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection

log = logging.getLogger(__name__)


# Mapping ``RegisteredApp.source_kind`` → set of acceptable
# ``SourceConnection.kind`` values. We accept any connection that
# can authenticate against the same host; preference order picks the
# App-install token first when both an OAuth-user token and an
# App-install token exist in the same org (App tokens don't expire
# silently and are scoped per-installation).
_KIND_PREFERENCE: dict[str, tuple[str, ...]] = {
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
    """

    status: str
    changes: ResyncChanges
    error: str | None = None


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


def _pick_source_connection(app: RegisteredApp) -> SourceConnection | None:
    """Pick the best ``SourceConnection`` for ``app``.

    Same org, not soft-deleted, not orphaned, active. Among matches,
    prefer the most specific credential kind for the source host
    (App-install > OAuth-user > PAT for GitHub; OAuth-user > PAT
    for GitLab). Returns None when no usable connection exists —
    the caller surfaces that as ``fetch_failed``.
    """
    accepted_kinds = _KIND_PREFERENCE.get(app.source_kind, ())
    if not accepted_kinds:
        return None

    qs = SourceConnection.objects.filter(
        organization_id=app.organization_id,
        kind__in=accepted_kinds,
        is_active=True,
        is_orphaned=False,
        deleted_at__isnull=True,
    )

    # Stable selection: rank by preference order, then by oldest
    # (lowest pk) so re-runs against the same app pick the same
    # connection.
    rows = list(qs)
    if not rows:
        return None

    rank = {kind: i for i, kind in enumerate(accepted_kinds)}
    rows.sort(key=lambda r: (rank.get(r.kind, len(accepted_kinds)), r.pk))
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

    deploy_branch = app.deploy_branch or app.default_branch or "main"
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
            error=(f"{manifest_path!r} not found on {deploy_branch!r} of " f"{app.source_repo!r}"),
        )

    # Parse the repo content first. A repo with broken TOML is a
    # ``fetch_failed`` from the operator's perspective — we never
    # silently apply garbage and never clobber the DB.
    try:
        repo_manifest = _normalize_text(repo_text)
    except ManifestError as exc:
        return ResyncResult(
            status="fetch_failed",
            changes=ResyncChanges(),
            error=f"repo manifest failed to parse: {exc}",
        )

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
            fields += ["updated_at", "version"]
            app.save(update_fields=fields)
        return ResyncResult(status="in_sync", changes=ResyncChanges())

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
        if staged.strip() and repo_text.strip() == staged.strip():
            app.manifest_raw_staged = ""
            app.save(
                update_fields=[
                    "manifest_raw_staged",
                    "last_synced_hash",
                    "last_resync_at",
                    "updated_at",
                    "version",
                ]
            )
        else:
            app.save(
                update_fields=[
                    "last_synced_hash",
                    "last_resync_at",
                    "updated_at",
                    "version",
                ]
            )

    return ResyncResult(status="applied", changes=changes)


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
            f"{changes.env_keys_changed} env " f"{'keys' if changes.env_keys_changed != 1 else 'key'} changed"
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
            f"{changes.schedules_changed} schedule" f"{'s' if changes.schedules_changed != 1 else ''} changed"
        )

    # Capitalize the first segment; period at the end.
    summary = ", ".join(parts)
    if summary:
        summary = summary[0].upper() + summary[1:]
    return summary + "."
