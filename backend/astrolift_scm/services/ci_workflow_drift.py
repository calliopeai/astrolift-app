"""Drift computation + observe-only reconcile for the managed CI workflow (#1210).

Phase 2 of the bidirectional versioned sync. Where Phase 1
(:mod:`astrolift_scm.services.workflow_sync`) *pushes* the rendered
workflow onto the repo and records what it stamped, this module *reads*
the repo back and classifies how the repo file stands relative to what
the platform last synced and what it renders today.

Two layers, kept apart on purpose:

* :func:`compute_sync_state` — a PURE function (no network, no ORM). It
  takes the repo file text, the persisted sync record, and the current
  template version and returns a :class:`SyncState`. Every branch is
  documented; it is the single place the drift vocabulary is decided.

* :func:`fetch_repo_ci_workflow` and the ``reconcile`` / ``evaluate`` /
  ``adopt`` helpers — the network-facing layer. They REUSE Phase 1's
  fetch plumbing (``_pick_source_connection`` + ``_render_and_path`` +
  the providers' ``fetch_file``) so there is exactly one place per host
  that knows how to read a file, and persist the observed state back
  into ``RegisteredApp.ci_workflow_state`` (drift lives in the JSON, no
  new column).

The Phase 2 helpers here never write to a repo or open a PR — they only
observe and flag. The manual "fix it" actions (resync / adopt) live in
the GraphQL mutation layer.

Phase 3 (#1211) adds the outbound fleet sweep at the bottom of this
module (:func:`reconcile_one_ci_workflow` + :func:`sweep_ci_workflows`).
It still does not write to a repo itself: it classifies each managed app
with the same pure :func:`compute_sync_state`, and for the SAFE states
only (``template_stale`` / ``absent``) DELEGATES the push to Phase 1's
:func:`~astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo`.
Operator hand-edits (``repo_drift`` / ``conflict``) are recorded but never
clobbered.
"""

from __future__ import annotations

import dataclasses
import enum
import logging

from django.utils import timezone

from astrolift_scm.ci_templates import (
    TEMPLATE_VERSION,
    content_hash,
    git_blob_sha,
    parse_stamp,
)

logger = logging.getLogger(__name__)


class SyncState(enum.StrEnum):
    """Where the repo's managed CI workflow file stands vs. the platform.

    ``StrEnum`` (the repo's enum convention) so the value serializes
    straight into the ``ci_workflow_state`` JSON and the GraphQL string
    field, and compares equal to its wire string.
    """

    # Repo file matches what we last synced AND our template hasn't moved on.
    IN_SYNC = "in_sync"
    # Repo file still matches what we synced, but the platform renders a newer
    # template — a resync would update the file. The repo itself is untouched.
    TEMPLATE_STALE = "template_stale"
    # Repo file diverged from what we synced (someone edited it), but our
    # template is unchanged — the divergence is entirely repo-side.
    REPO_DRIFT = "repo_drift"
    # Repo file diverged AND our template advanced — both sides moved, so a
    # blind resync would clobber the repo edit. Needs an operator decision.
    CONFLICT = "conflict"
    # No managed workflow file exists on the branch tip today.
    ABSENT = "absent"
    # Could not classify (indeterminate inputs, or the repo couldn't be read).
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Pure drift classification (no network, no ORM)
# ---------------------------------------------------------------------------


def compute_sync_state(
    *,
    repo_file_text: str | None,
    persisted_state: dict,
    current_template_version: int,
) -> SyncState:
    """Classify the repo's managed workflow file against our sync record.

    PURE: no I/O. Inputs:

    * ``repo_file_text`` — the workflow file's text on the branch tip, or
      ``None`` when the file is not present.
    * ``persisted_state`` — the ``ci_workflow_state`` blob (Phase 1 keys:
      ``synced_hash`` / ``synced_blob_sha`` / …), plus the caller-injected
      ``synced_template_version`` (the ``ci_workflow_template_version``
      column value — it lives on the model, not in the blob, so callers
      inject it; :func:`_persisted_state_with_version` does this wiring).
    * ``current_template_version`` — the version the platform renders now
      (:data:`TEMPLATE_VERSION`).

    The comparison mirrors how Phase 1 stamps and persists: ``synced_hash``
    is ``content_hash`` of the rendered body with the stamp line stripped,
    and :func:`content_hash` strips the stamp before hashing, so hashing a
    file fetched back from the repo round-trips to the same digest — a
    stamp-only difference (e.g. a version bump) never registers as body
    drift. That keeps the two axes independent: body change (repo drift)
    vs. version change (template staleness).
    """
    # Defensive: without a usable current template version we cannot judge
    # staleness — report UNKNOWN rather than invent a determinate state.
    if not isinstance(current_template_version, int) or current_template_version <= 0:
        return SyncState.UNKNOWN

    # The managed file isn't on the branch tip. Covers "never created" and
    # "was synced, since deleted" alike — either way there is no file today.
    if repo_file_text is None:
        return SyncState.ABSENT

    state = persisted_state if isinstance(persisted_state, dict) else {}
    synced_hash = state.get("synced_hash") or ""
    synced_version = state.get("synced_template_version")

    parsed = parse_stamp(repo_file_text)
    repo_version = parsed.version  # None when the file carries no astrolift stamp
    repo_hash = content_hash(repo_file_text)

    # Our template moved past what we last stamped, OR the repo file's own
    # stamp is behind the current template. Either means a resync would bump
    # the file to a newer generation.
    template_advanced = bool(
        (synced_version is not None and current_template_version > synced_version)
        or (repo_version is not None and repo_version < current_template_version)
    )

    # No baseline at all: we've never versioned-synced this app, yet a file
    # exists at the managed path — someone else authored it. Flag as repo
    # drift; adopt (re-baseline) or resync (overwrite) resolves it. We don't
    # escalate to CONFLICT here because with no baseline there is nothing to
    # say the template "advanced" relative to.
    if not synced_hash:
        return SyncState.REPO_DRIFT

    repo_unchanged = repo_hash == synced_hash

    if repo_unchanged and not template_advanced:
        # Byte-for-byte what we synced, same template generation.
        return SyncState.IN_SYNC
    if repo_unchanged and template_advanced:
        # Untouched repo-side, but our template rolled forward.
        return SyncState.TEMPLATE_STALE
    if not repo_unchanged and not template_advanced:
        # Repo edited under a still-current template — pure repo drift.
        return SyncState.REPO_DRIFT
    # Repo edited AND template advanced — both sides moved; needs a decision.
    return SyncState.CONFLICT


# ---------------------------------------------------------------------------
# Network-facing fetch (reuses Phase 1 plumbing)
# ---------------------------------------------------------------------------


class CiWorkflowFetchError(Exception):
    """A repo read failed. ``code`` carries the provider's classification
    (``RATE_LIMITED`` / ``AUTH_FAILED`` / ``NETWORK`` / ``NO_CONNECTION`` /
    ``UNSUPPORTED`` / …) so callers can treat a transient rate limit
    differently from a hard auth failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def fetch_repo_ci_workflow(app, *, ref: str | None = None) -> str | None:
    """Read the managed workflow file for ``app`` at ``ref`` (default: the
    app's deploy branch tip).

    Reuses Phase 1's org-level connection picker and per-host path resolver
    rather than re-deriving either — the only new thing here is a read
    instead of a compare-and-write. Returns the file text, or ``None`` when
    the file is absent. Raises :class:`CiWorkflowFetchError` on any host
    error (the provider's ``RATE_LIMITED`` vs ``AUTH_FAILED`` distinction is
    preserved via ``code``).
    """
    from astrolift_scm.providers import ProviderError, fetch_file
    from astrolift_scm.services.workflow_sync import (
        _pick_source_connection,
        _render_and_path,
    )

    if not app.source_repo:
        raise CiWorkflowFetchError("NO_SOURCE_REPO", "app has no source repo configured; nothing to read")

    connection = _pick_source_connection(app)
    if connection is None:
        raise CiWorkflowFetchError(
            "NO_CONNECTION",
            "no active source connection for this app's org; connect one to check drift",
        )

    _body, path = _render_and_path(app)
    branch = (app.deploy_branch or "main").strip() or "main"
    fetch_ref = ref or branch
    try:
        return fetch_file(
            connection,
            repo_full_name=app.source_repo,
            path=path,
            ref=fetch_ref,
        )
    except ProviderError as exc:
        raise CiWorkflowFetchError(exc.code, exc.message) from exc


# ---------------------------------------------------------------------------
# Persistence of the OBSERVED state (drift lives in the JSON, no new column)
# ---------------------------------------------------------------------------


def _persisted_state_with_version(app) -> dict:
    """The app's ``ci_workflow_state`` blob with the synced template version
    injected from the column so the pure classifier sees one dict.

    The version lives on ``ci_workflow_template_version`` (a column), not in
    the blob; injecting it here (never persisting it back) keeps the column
    authoritative while giving :func:`compute_sync_state` a self-contained
    input.
    """
    blob = dict(app.ci_workflow_state or {})
    blob["synced_template_version"] = app.ci_workflow_template_version
    return blob


def _persist_observed_state(app, state: SyncState, *, detail: str = "") -> None:
    """Record the observed drift state onto the app WITHOUT touching the repo.

    Writes only ``state`` + ``checked_at`` (+ optional ``detail``) into the
    existing ``ci_workflow_state`` blob; the Phase 1 baseline keys
    (``synced_hash`` / ``synced_blob_sha`` / ``synced_at`` / …) are left
    intact. This is an observe-only flag — never a push, never a PR.
    """
    blob = dict(app.ci_workflow_state or {})
    blob["state"] = state.value
    blob["checked_at"] = timezone.now().isoformat()
    blob["detail"] = detail
    app.ci_workflow_state = blob
    app.save(update_fields=["ci_workflow_state", "updated_at", "version"])


# ---------------------------------------------------------------------------
# Observe-only inbound reconcile (push webhook) + manual recompute
# ---------------------------------------------------------------------------


def reconcile_ci_workflow_on_push(app, *, branch: str | None, pushed_sha: str | None) -> None:
    """Observe-only reconcile driven by a source-push webhook (#1210).

    Fires on a push to the app's **deploy branch only**: fetches the managed
    file at the pushed SHA, classifies it via :func:`compute_sync_state`, and
    flags the result into ``ci_workflow_state['state']`` (+ ``checked_at``).

    FLAG ONLY — it never pushes, never opens a PR, never edits the repo.
    Best-effort: a non-deploy-branch push is ignored, and any fetch/compute
    error is logged and leaves the prior state untouched (a transient host
    error must not overwrite a good reading or block the webhook ack).
    """
    deploy_branch = (app.deploy_branch or "main").strip() or "main"
    if (branch or "").strip() != deploy_branch:
        # Only the deploy branch carries the managed file; other branches
        # (feature branches, tags) don't change what deploys.
        return

    try:
        repo_text = fetch_repo_ci_workflow(app, ref=pushed_sha or deploy_branch)
    except CiWorkflowFetchError as exc:
        # Expected host errors (auth / rate limit / network / no connection):
        # leave the prior reading in place and move on.
        logger.warning(
            "ci-workflow reconcile: fetch failed for app=%s (%s): %s",
            getattr(app, "pk", "?"),
            exc.code,
            exc.message,
        )
        return
    except Exception:
        # Anything unexpected on the read path (e.g. a credential that won't
        # decrypt, a render hiccup) is still best-effort here — flag-only must
        # never disturb the webhook ack. Log at WARNING and keep prior state.
        logger.warning(
            "ci-workflow reconcile: unexpected read error for app=%s",
            getattr(app, "pk", "?"),
            exc_info=True,
        )
        return

    try:
        state = compute_sync_state(
            repo_file_text=repo_text,
            persisted_state=_persisted_state_with_version(app),
            current_template_version=TEMPLATE_VERSION,
        )
    except Exception:
        logger.warning(
            "ci-workflow reconcile: classify failed for app=%s",
            getattr(app, "pk", "?"),
            exc_info=True,
        )
        return

    _persist_observed_state(app, state)


def evaluate_and_persist_sync_state(app) -> SyncState:
    """Manual "recompute now": fetch the repo file, classify, persist, return.

    Backs the ``refreshCiWorkflowSyncStatus`` mutation. A transient rate
    limit must NOT poison the drift state into a hard failure — a
    ``RATE_LIMITED`` fetch is recorded as :attr:`SyncState.UNKNOWN` with a
    detail (state genuinely undetermined this round) rather than raised. All
    other host errors raise :class:`CiWorkflowFetchError` for the caller to
    surface as a failure envelope.
    """
    try:
        repo_text = fetch_repo_ci_workflow(app)
    except CiWorkflowFetchError as exc:
        if exc.code == "RATE_LIMITED":
            _persist_observed_state(
                app,
                SyncState.UNKNOWN,
                detail="rate limited reading the repo; drift undetermined — try again shortly",
            )
            return SyncState.UNKNOWN
        raise

    state = compute_sync_state(
        repo_file_text=repo_text,
        persisted_state=_persisted_state_with_version(app),
        current_template_version=TEMPLATE_VERSION,
    )
    _persist_observed_state(app, state)
    return state


# ---------------------------------------------------------------------------
# Adopt: re-baseline onto the repo's current file WITHOUT pushing
# ---------------------------------------------------------------------------


class CiWorkflowAdoptError(Exception):
    """Adopt precondition failed (no file in the repo to adopt)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclasses.dataclass(frozen=True, slots=True)
class AdoptResult:
    """Outcome of :func:`adopt_repo_ci_workflow` — the new baseline digests."""

    synced_hash: str
    synced_blob_sha: str
    template_version: int


# The stored repo copy is a CI workflow file: GitHub caps a workflow at
# well under this, and the platform's own render is ~4 KiB. A file past the
# cap is refused rather than truncated, because a half-file shown as "what
# your repo has" is worse than an error saying it was too big to keep.
MAX_STORED_REPO_TEXT_BYTES: int = 64 * 1024


def pull_repo_ci_workflow(app) -> AdoptResult:
    """Bring the repo's workflow file INTO the platform and make it the baseline.

    The one direction that was missing. Every other action on this file
    writes outward: :func:`~astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo`
    renders the template and puts it, and the sweep does the same. The
    inbound half existed only as a digest re-baseline -- it stopped the
    drift badge and threw the file away, so an operator's own workflow was
    never anywhere the platform could show it.

    This stores the text under ``ci_workflow_state["repo_text"]`` (with the
    ref and byte length it was read at) alongside the re-baselined digests,
    so ``astroliftApp.ciWorkflowSyncStatus`` can hand the UI the repo copy
    to diff against the current render. Nothing is pushed.

    Raises :class:`CiWorkflowAdoptError` when there is no file to pull, or
    when the file is larger than :data:`MAX_STORED_REPO_TEXT_BYTES`.
    """
    repo_text = fetch_repo_ci_workflow(app)
    if repo_text is None:
        raise CiWorkflowAdoptError(
            "ABSENT",
            "no managed CI workflow file exists in the repo to pull; push one first",
        )

    size = len(repo_text.encode("utf-8"))
    if size > MAX_STORED_REPO_TEXT_BYTES:
        raise CiWorkflowAdoptError(
            "TOO_LARGE",
            f"the repo's workflow file is {size} bytes, over the "
            f"{MAX_STORED_REPO_TEXT_BYTES}-byte limit the platform keeps; "
            "trim it or manage it outside Astrolift",
        )

    result = _rebaseline_on_repo_copy(app, repo_text, store_text=True)
    return result


def adopt_repo_ci_workflow(app) -> AdoptResult:
    """Re-baseline onto the repo's file WITHOUT keeping a copy of it.

    Retained for the deprecated ``adoptRepoCiWorkflow`` mutation. Identical
    to :func:`pull_repo_ci_workflow` except that the file's text is not
    stored, which is the whole reason a pull was added: adopting told the
    platform to stop flagging a file it then could not show anyone.
    """
    repo_text = fetch_repo_ci_workflow(app)
    if repo_text is None:
        raise CiWorkflowAdoptError(
            "ABSENT",
            "no managed CI workflow file exists in the repo to adopt; push one first",
        )
    return _rebaseline_on_repo_copy(app, repo_text, store_text=False)


def _rebaseline_on_repo_copy(app, repo_text: str, *, store_text: bool) -> AdoptResult:
    """Point the sync record at ``repo_text`` and flag ``in_sync``.

    Shared by the pull and the deprecated adopt so there is one place that
    decides what "the repo copy is now the baseline" means on the record.
    """
    from astrolift_scm.services.workflow_sync import _render_and_path

    _body, path = _render_and_path(app)
    parsed = parse_stamp(repo_text)
    # Honor the repo file's own stamp version when present; an unstamped file
    # we take is declared current (so it reads IN_SYNC, not perpetually
    # stale) -- the column stays non-null to preserve "null == never pushed".
    version = parsed.version if parsed.version is not None else TEMPLATE_VERSION

    now = timezone.now().isoformat()
    blob = dict(app.ci_workflow_state or {})
    blob["synced_hash"] = content_hash(repo_text)
    blob["synced_blob_sha"] = git_blob_sha(repo_text.encode("utf-8"))
    blob["path"] = path
    blob["state"] = SyncState.IN_SYNC.value
    blob["synced_at"] = now
    blob["checked_at"] = now
    blob["detail"] = ""
    # Taking the repo copy supersedes any pending platform push PR.
    blob.pop("pr_url", None)

    if store_text:
        blob["repo_text"] = repo_text
        blob["repo_text_bytes"] = len(repo_text.encode("utf-8"))
        blob["repo_text_ref"] = (app.deploy_branch or "main").strip() or "main"
        blob["repo_text_pulled_at"] = now
    else:
        # An adopt that keeps no text must not leave a stale one behind
        # claiming to be what the repo has.
        for key in ("repo_text", "repo_text_bytes", "repo_text_ref", "repo_text_pulled_at"):
            blob.pop(key, None)

    app.ci_workflow_template_version = version
    app.ci_workflow_state = blob
    app.save(
        update_fields=[
            "ci_workflow_template_version",
            "ci_workflow_state",
            "updated_at",
            "version",
        ]
    )
    return AdoptResult(
        synced_hash=blob["synced_hash"],
        synced_blob_sha=blob["synced_blob_sha"],
        template_version=version,
    )


# ---------------------------------------------------------------------------
# Phase 3 (#1211): outbound fleet resync sweep
# ---------------------------------------------------------------------------
#
# A held (opt-in) sweep that recomputes drift for every MANAGED app and
# auto-pushes ONLY the safe states. It reuses the exact Phase 1/2 pieces:
#   * fetch    → :func:`fetch_repo_ci_workflow` (Phase 2 network read)
#   * classify → :func:`compute_sync_state` (Phase 2 pure state machine)
#   * observe  → :func:`_persist_observed_state` (Phase 2 flag-only persist)
#   * push     → Phase 1's ``sync_workflow_file_to_repo`` (delegated, and it
#                re-stamps the sync record itself)
# No drift/fetch/push logic is re-implemented here.


# The only hosts Phase 1 can render + push. An app can only carry a
# non-null ``ci_workflow_template_version`` (the "managed" signal) if Phase 1
# already synced it, which never happens for git_url / direct_upload — but we
# filter defensively so the sweep never even fetches for an unpushable host.
_SWEEPABLE_SOURCE_KINDS: tuple[str, ...] = ("github", "gitlab", "bitbucket", "gitea")


class ReconcileOutcome(enum.StrEnum):
    """What the per-app reconcile did this pass — one per app, mutually
    exclusive, so the sweep summary is just a tally.

    ``PUSHED`` — the app was ``template_stale`` / ``absent`` and the current
    template was reconciled onto the repo (Phase 1 push landed / matched /
    opened a PR). The remaining members mirror the observe-only
    :class:`SyncState` values that are NEVER pushed (operator hand-edits are
    recorded, not clobbered). ``SKIPPED`` — a fetch error / rate limit, or a
    push that hit a transient host error: the prior state is left untouched.
    ``FAILED`` — an unexpected error the sweep isolated to this app.
    """

    PUSHED = "pushed"
    IN_SYNC = "in_sync"
    REPO_DRIFT = "repo_drift"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"
    FAILED = "failed"


# The observe-only states → their outcome tally bucket. TEMPLATE_STALE /
# ABSENT are handled separately (they push), so they are intentionally absent.
_OUTCOME_BY_STATE: dict[SyncState, ReconcileOutcome] = {
    SyncState.IN_SYNC: ReconcileOutcome.IN_SYNC,
    SyncState.REPO_DRIFT: ReconcileOutcome.REPO_DRIFT,
    SyncState.CONFLICT: ReconcileOutcome.CONFLICT,
    SyncState.UNKNOWN: ReconcileOutcome.UNKNOWN,
}


@dataclasses.dataclass(frozen=True, slots=True)
class CiWorkflowResyncSummary:
    """Per-sweep tally. ``scanned`` == sum of every other field (each app
    lands in exactly one bucket). Returned by the sweep + surfaced by the
    ``resyncAllAstroliftCiWorkflows`` mutation and the tick activity."""

    scanned: int
    pushed: int
    in_sync: int
    repo_drift: int
    conflict: int
    unknown: int
    skipped: int
    failed: int


def reconcile_one_ci_workflow(app) -> ReconcileOutcome:
    """Recompute drift for ONE managed app and auto-push only the safe states.

    * Fetch the repo file (Phase 2). Any :class:`CiWorkflowFetchError`
      (``RATE_LIMITED`` included) → :attr:`ReconcileOutcome.SKIPPED`: leave the
      prior persisted state untouched, never raise.
    * Classify with the pure :func:`compute_sync_state` (Phase 2).
    * ``TEMPLATE_STALE`` / ``ABSENT`` → the file is safe to overwrite (repo
      side untouched, only the template moved on / the file is missing), so
      DELEGATE to Phase 1's ``sync_workflow_file_to_repo`` — which pushes and
      re-stamps the sync record. A ``WorkflowSyncError`` (no connection /
      unsupported host) or a ``fetch_failed`` push result → ``SKIPPED``.
    * ``REPO_DRIFT`` / ``CONFLICT`` / ``IN_SYNC`` / ``UNKNOWN`` → NEVER push
      (a blind push would clobber an operator hand-edit). Flag the observed
      state via the Phase 2 observe-only persist and move on.
    """
    try:
        repo_text = fetch_repo_ci_workflow(app)
    except CiWorkflowFetchError as exc:
        # Transient / expected host errors (rate limit, auth, network, no
        # connection): skip harmlessly, leaving the prior reading in place.
        logger.info(
            "ci-workflow resync: skipping app=%s (%s): %s",
            getattr(app, "pk", "?"),
            exc.code,
            exc.message,
        )
        return ReconcileOutcome.SKIPPED

    state = compute_sync_state(
        repo_file_text=repo_text,
        persisted_state=_persisted_state_with_version(app),
        current_template_version=TEMPLATE_VERSION,
    )

    if state in (SyncState.TEMPLATE_STALE, SyncState.ABSENT):
        # SAFE to push: repo side is byte-for-byte what we synced (or the file
        # is gone), so overwriting with the current template can't destroy an
        # operator edit. Reuse Phase 1's push wholesale — it re-stamps too.
        from astrolift_scm.services.workflow_sync import (
            WorkflowSyncError,
            sync_workflow_file_to_repo,
        )

        try:
            result = sync_workflow_file_to_repo(app)
        except WorkflowSyncError as exc:
            logger.info(
                "ci-workflow resync: push skipped for app=%s: %s",
                getattr(app, "pk", "?"),
                exc.message,
            )
            return ReconcileOutcome.SKIPPED
        if result.status == "fetch_failed":
            # Host was unreachable / token rejected mid-push — nothing landed.
            # Treat as a harmless skip; the prior state stands.
            logger.info(
                "ci-workflow resync: push fetch_failed for app=%s: %s",
                getattr(app, "pk", "?"),
                result.error,
            )
            return ReconcileOutcome.SKIPPED
        return ReconcileOutcome.PUSHED

    # Observe-only states: record the reading, never touch the repo.
    _persist_observed_state(app, state)
    return _OUTCOME_BY_STATE[state]


def managed_ci_workflow_apps():
    """Queryset of apps with a MANAGED CI workflow: a pushable source repo and
    a non-null ``ci_workflow_template_version`` (Phase 1 synced them at least
    once). Soft-deleted rows are excluded by the default manager. Ordered by
    ``pk`` for deterministic, bounded iteration."""
    from astrolift_registry.models import RegisteredApp

    return (
        RegisteredApp.objects.filter(
            ci_workflow_template_version__isnull=False,
            source_kind__in=_SWEEPABLE_SOURCE_KINDS,
        )
        .exclude(source_repo="")
        .order_by("pk")
    )


def sweep_ci_workflows(*, limit: int | None = None) -> CiWorkflowResyncSummary:
    """Recompute drift for every managed app and auto-push the safe states.

    Fans out over :func:`managed_ci_workflow_apps` (streamed with
    ``.iterator()`` so a large fleet stays memory-bounded), calling
    :func:`reconcile_one_ci_workflow` per app under a per-app ``try/except`` so
    one app's unexpected failure is isolated to a ``failed`` tally and never
    aborts the sweep. ``limit`` caps the number of apps processed this pass
    (used by the on-demand mutation to bound a synchronous request; the
    scheduled tick passes ``None`` to sweep the whole fleet).
    """
    qs = managed_ci_workflow_apps()
    if limit is not None:
        qs = qs[:limit]

    tally: dict[ReconcileOutcome, int] = dict.fromkeys(ReconcileOutcome, 0)
    scanned = 0
    for app in qs.iterator():
        scanned += 1
        try:
            outcome = reconcile_one_ci_workflow(app)
        except Exception:  # noqa: BLE001 — isolate any per-app failure
            logger.warning(
                "ci-workflow resync: unexpected error for app=%s",
                getattr(app, "pk", "?"),
                exc_info=True,
            )
            tally[ReconcileOutcome.FAILED] += 1
            continue
        tally[outcome] += 1

    return CiWorkflowResyncSummary(
        scanned=scanned,
        pushed=tally[ReconcileOutcome.PUSHED],
        in_sync=tally[ReconcileOutcome.IN_SYNC],
        repo_drift=tally[ReconcileOutcome.REPO_DRIFT],
        conflict=tally[ReconcileOutcome.CONFLICT],
        unknown=tally[ReconcileOutcome.UNKNOWN],
        skipped=tally[ReconcileOutcome.SKIPPED],
        failed=tally[ReconcileOutcome.FAILED],
    )
