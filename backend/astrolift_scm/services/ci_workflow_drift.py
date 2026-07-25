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

Nothing in this module ever writes to a repo or opens a PR — it only
observes and flags. The manual "fix it" actions (resync / adopt) live in
the GraphQL mutation layer.
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


def adopt_repo_ci_workflow(app) -> AdoptResult:
    """Declare the repo's CURRENT workflow file the authoritative baseline.

    Fetches the repo file and re-points the app's sync record
    (``synced_hash`` / ``synced_blob_sha`` / ``ci_workflow_template_version``)
    at it, then flags ``state = in_sync``. Clears a ``repo_drift`` /
    ``conflict`` by accepting the repo copy — WITHOUT pushing anything.

    Per the epic's caveat, adopt does NOT import the repo file's config; it
    only re-baselines the stamp tracking so the platform stops flagging a
    file it now considers authoritative. Raises
    :class:`CiWorkflowAdoptError` when there is no file to adopt.
    """
    repo_text = fetch_repo_ci_workflow(app)
    if repo_text is None:
        raise CiWorkflowAdoptError(
            "ABSENT",
            "no managed CI workflow file exists in the repo to adopt; sync one first",
        )

    from astrolift_scm.services.workflow_sync import _render_and_path

    _body, path = _render_and_path(app)
    parsed = parse_stamp(repo_text)
    # Honor the repo file's own stamp version when present; an unstamped file
    # we adopt is declared current (so it reads IN_SYNC, not perpetually
    # stale) — the column stays non-null to preserve "null == never synced".
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
    # Adopting the repo copy supersedes any pending platform sync PR.
    blob.pop("pr_url", None)

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
