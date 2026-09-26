"""Literal app-secret resolution shared by the secrets UI and the deploy path (#1758).

App-wide ``[env]`` literals (written by the setAppSecret / rotateAppSecret /
deleteAppSecret / bulkImportAppSecrets mutations) live in the manifest text
buffer, not a per-secret DB row -- see ``AppSecretMetadata``'s docstring.
Two independent consumers ask "which keys are visible in this environment":

* ``astrolift_services.schema.queries._list_app_secrets`` -- the UI's
  secret list.
* ``core.app_deploy.render_resources_for_deployment`` /
  ``astrolift_workflows.activities.app_lifecycle._update_secrets_sync`` --
  what actually reaches a workload's envFrom.

Scope filtering (``AppSecretMetadata.scope`` vs. the queried environment)
is a visibility boundary -- a preview-scoped secret leaking into
production (or vice versa) is a real leak, not a display glitch -- so both
consumers share this one predicate instead of maintaining their own copy.
The deploy path is stricter than the list in two ways: under secret
approval it deploys only approved staged changes, and it recognises a
preview by identity rather than by build status (#1758 review, H1/H2).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterable, Mapping

from django.conf import settings
from django.utils.crypto import constant_time_compare, salted_hmac

from astrolift_lifecycle.models.preview_environment import PreviewEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_services.models import AppSecretMetadata, SecretChangeProposal
from astrolift_services.secret_metadata_ops import scope_in_force

log = logging.getLogger(__name__)


_SCOPE_LITERALS = frozenset({"all", "production", "preview"})
_SCOPE_PREVIEW_BRANCH_PREFIX = "preview:"


def valid_scope_literal_error(scope: str) -> str | None:
    """Why ``scope`` isn't a value ``allowed_scopes_for_env`` can ever match,
    or ``None`` (#1923).

    The recognised set is exactly ``all``, ``production``, ``preview``, and
    ``preview:<branch>``. Anything else -- a typo, or an actual environment
    name (an app can perfectly well name an environment "production" or
    "staging"; scope is not that) -- silently hides the key from every
    environment: it doesn't match either branch of ``allowed_scopes_for_env``,
    ever, for any env."""
    if scope in _SCOPE_LITERALS:
        return None
    if scope.startswith(_SCOPE_PREVIEW_BRANCH_PREFIX) and scope[len(_SCOPE_PREVIEW_BRANCH_PREFIX) :]:
        return None
    return (
        f"scope {scope!r} is not a recognised value; use 'all', 'production', 'preview', "
        "'preview:<branch>', or omit it to keep the current scope -- this is not an "
        "environment name"
    )


def allowed_scopes_for_env(env_name: str, preview_env_branches: dict[str, str]) -> frozenset[str]:
    """Compute the set of scope values that a secret must have to be
    visible in ``env_name``.

    Preview envs accept ``all``, ``preview``, and ``preview:<branch>``.
    All other envs (production, staging, …) accept ``all`` and
    ``production``.
    """
    if env_name in preview_env_branches:
        branch = preview_env_branches[env_name]
        return frozenset({"all", "preview", f"preview:{branch}"})
    return frozenset({"all", "production"})


_BASE_STAMP_SALT = "astrolift.secret-proposal.base-raw"


def _base_digest(app_guid: str, key: str, value: str | None, secret: str | None = None) -> str:
    # json.dumps keeps an absent key (null) apart from an empty value ("").
    # The app and the key are in the message so a stamp vouches for one key
    # of one app: the same value elsewhere hashes differently, so a stamp
    # neither carries over nor shows that two keys share a value.
    message = json.dumps([app_guid, key, value])
    return salted_hmac(_BASE_STAMP_SALT, message, secret=secret, algorithm="sha256").hexdigest()


def base_raw_digest(app, key: str, value: str | None) -> str:
    """Stamp for a key's ``manifest_raw`` value when a proposal is applied.

    Keyed, because the proposal payload is readable through the GraphQL
    proposal type and a plain hash of a short old value could be
    brute-forced."""
    return _base_digest(str(app.guid), key, value)


def _base_raw_matches(stamp: str | None, app, key: str, value: str | None) -> bool:
    if not stamp:
        return False
    # Fallback keys too, so a SECRET_KEY rotation done the Django way does
    # not quietly void every pending approval.
    secrets = (settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", ()))
    return any(
        constant_time_compare(stamp, _base_digest(str(app.guid), key, value, secret)) for secret in secrets
    )


def _latest_applied_literal_changes(app) -> dict[str, tuple[str | None, str | None, str]]:
    """Per key, what the most recent applied SET/DELETE proposal made it
    (the value it set, or None when it deleted the key), with the stamp of
    the ``manifest_raw`` value it was applied against and the proposal's
    guid."""
    latest: dict[str, tuple[str | None, str | None, str]] = {}
    applied = (
        SecretChangeProposal.objects.filter(
            registered_app=app,
            status=SecretChangeProposal.Status.APPLIED.value,
            op__in=(SecretChangeProposal.Op.SET.value, SecretChangeProposal.Op.DELETE.value),
        )
        .order_by("-applied_at", "-pk")
        .values_list("op", "payload", "guid")
    )
    for op, payload, guid in applied:
        payload = payload or {}
        key = str(payload.get("key") or "").strip()
        if not key or key in latest:
            continue
        value = None if op == SecretChangeProposal.Op.DELETE.value else str(payload.get("value") or "")
        latest[key] = (value, payload.get("base_raw_digest"), str(guid))
    return latest


@dataclasses.dataclass(frozen=True, slots=True)
class LiteralApproval:
    # Keys no applied proposal authorizes, in the order they were asked about.
    unapproved: list[str]
    # Guids of the applied proposals that authorize the rest.
    proposal_ids: list[str]


def approved_literal_changes(app, changes: Mapping[str, object]) -> LiteralApproval:
    """Which ``[env]`` changes applied secret-change proposals authorize.

    ``changes`` maps each changed key to its value after the edit, None when
    the edit removes it. The rule is the one ``_deployable_literals``
    applies at deploy time: the latest applied SET/DELETE for the key
    produced exactly that value, from the ``manifest_raw`` value the key
    still has. So an approval authorizes only the change it was approved
    against; re-staging a value the app has since rotated away from is not
    covered by the approval that first set it. ``applyStagedManifest``
    uses this to move approved changes into ``manifest_raw`` on an app
    with no source repo (#1759). A value that isn't a string never matches:
    a proposal only ever writes strings.
    """
    raw = read_app_env(app.manifest_raw or "")
    approved = _latest_applied_literal_changes(app)
    unapproved: list[str] = []
    proposal_ids: list[str] = []
    for key, after in changes.items():
        change = approved.get(key)
        if change is None or change[0] != after or not _base_raw_matches(change[1], app, key, raw.get(key)):
            unapproved.append(key)
        else:
            proposal_ids.append(change[2])
    return LiteralApproval(unapproved=unapproved, proposal_ids=proposal_ids)


def _deployable_literals(app) -> dict[str, str]:
    """The app-wide ``[env]`` literals a deploy may put in front of a workload.

    Without secret approval that is ``manifest_raw_staged`` when set, else
    ``manifest_raw``. The set/rotate/delete/bulk-import mutations edit only
    the staged buffer, so a literal takes effect on the next deploy
    without a push-to-repo round trip (#1758).

    With approval on (#488) the staged buffer cannot be trusted on its
    own: ``updateManifest`` and ``bulkImportAppSecrets`` write it without
    creating a proposal (#1758 review, H1). So the deploy starts from
    ``manifest_raw``, and a staged change to a key is taken only when the
    latest applied proposal for that key produced exactly that change,
    from the ``manifest_raw`` value the key still has. Anything else keeps
    the ``manifest_raw`` value. Anchoring on the staged buffer, rather
    than replaying every applied proposal over ``manifest_raw``, keeps an
    old approval from overriding a later repo change to the same key: a
    pushed and synced draft clears the buffer. The base stamp covers the
    rest: re-staging a value the repo has since rotated out is not
    covered by the approval that first set it (#1758 re-review, 3). A
    proposal applied before stamps existed vouches for nothing.
    """
    raw = read_app_env(app.manifest_raw or "")
    if not app.manifest_raw_staged:
        return raw
    staged = read_app_env(app.manifest_raw_staged)
    if not app.requires_secret_approval:
        return staged

    pending = sorted(key for key in raw.keys() | staged.keys() if raw.get(key) != staged.get(key))
    if not pending:
        return raw
    approved = _latest_applied_literal_changes(app)
    out = dict(raw)
    unapproved: list[str] = []
    for key in pending:
        change = approved.get(key)
        if (
            change is None
            or change[0] != staged.get(key)
            or not _base_raw_matches(change[1], app, key, raw.get(key))
        ):
            unapproved.append(key)
        elif key in staged:
            out[key] = staged[key]
        else:
            out.pop(key, None)
    if unapproved:
        # Key names only; the UI lists them anyway. Without this an
        # operator sees the staged value in the secrets list and cannot
        # tell why the pod never got it.
        log.warning(
            "app %s requires secret approval; staged [env] changes to %s have no matching "
            "applied proposal and are not deployed",
            app.slug,
            ", ".join(unapproved),
        )
    return out


def literal_secrets_for_environment(app, environment) -> dict[str, str]:
    """App-wide ``[env]`` literal secrets visible to one ``AppEnvironment``:
    ``_deployable_literals`` filtered by scope.

    Filters out keys whose ``AppSecretMetadata.scope`` doesn't match the
    environment, with the same ``allowed_scopes_for_env`` rule the UI's
    secret list uses (#752).
    """
    literals = _deployable_literals(app)
    if not literals:
        return {}

    scopes: dict[tuple[str, str], str | None] = {
        (env_name, key): scope
        for env_name, key, scope in AppSecretMetadata.objects.filter(
            registered_app=app,
            key__in=list(literals.keys()),
            deleted_at__isnull=True,
        ).values_list("environment_name", "key", "scope")
    }
    # A preview by identity, not by current status: a FAILED or torn-down
    # preview (its row soft-deleted) is still a preview, and a status or
    # deleted_at filter here made it fall through to the production
    # branch and receive production-scoped secrets (#1758 review, H2).
    # The lineage FK also counts, for a preview whose row is gone.
    preview_branch = (
        PreviewEnvironment.all_objects.filter(app_environment=environment)
        .order_by("-created_at", "-pk")
        .values_list("branch", flat=True)
        .first()
    )
    is_preview = preview_branch is not None or environment.previewed_environment_id is not None
    preview_env_branches = {environment.name: preview_branch or ""} if is_preview else {}
    allowed = allowed_scopes_for_env(environment.name, preview_env_branches)

    out: dict[str, str] = {}
    for key, value in literals.items():
        if scope_in_force(scopes.get((environment.name, key)), scopes.get(("", key))) in allowed:
            out[key] = value
    return out


def snapshot_literal_secrets(deployment) -> dict[str, str]:
    """This deployment's literal `[env]` secrets, computed once and
    persisted on ``Deployment.secret_snapshot`` (#1957, #1923).

    ``render_resources_for_deployment`` and ``_update_secrets_sync`` used to
    each call ``literal_secrets_for_environment`` independently, so they
    could disagree if the staged buffer moved between the two calls within
    one deploy (one deciding the workload's envFrom references the literal
    Secret, the other deciding whether to write or delete it). The first
    caller for a given deployment row computes it live and stores the
    result; every later caller for the *same* row reads the stored value
    back instead of recomputing, so the whole deploy pipeline -- render,
    apply, update_secrets -- agrees on one answer for that row.

    This is also how a rollback restores what its target deployment ran
    with: ``create_rollback_deployment`` copies the target's
    ``secret_snapshot`` onto the new row before this ever runs for it, so
    the first (and only) call here finds it already populated and returns
    the target's values rather than live-recomputing against whatever is
    staged now.

    Presence of the ``"literals"`` key is what marks a row as already
    computed -- a legitimately empty dict (no literals visible to this
    environment) is a valid, final answer, not "not yet computed".
    """
    snapshot = deployment.secret_snapshot or {}
    if "literals" in snapshot:
        return dict(snapshot["literals"])
    literals = literal_secrets_for_environment(deployment.registered_app, deployment.app_environment)
    snapshot = {**snapshot, "literals": literals}
    deployment.secret_snapshot = snapshot
    deployment.save(update_fields=["secret_snapshot", "updated_at", "version"])
    return literals


def preview_branches_for_app(app, env_names: Iterable[str]) -> dict[str, str]:
    """Which of ``env_names`` are previews, by identity rather than current
    build status, and their branch -- batched across every name in one call
    (#1923).

    Mirrors the per-environment lookup ``literal_secrets_for_environment``
    uses (a FAILED or torn-down preview, or one known only by its
    ``previewed_environment`` lineage, still counts) so the UI's secret
    list and the deploy path never disagree about which environments are
    previews. The list used to run its own query filtered to
    BUILDING/RUNNING PreviewEnvironment rows, so a FAILED or torn-down
    preview fell through to the production branch and showed
    production-scoped keys the deploy already withheld (#1758 review, H2;
    #1923 review-fix pass).
    """
    from astrolift_lifecycle.models import AppEnvironment

    names = list(env_names)
    if not names:
        return {}
    envs = list(
        AppEnvironment.all_objects.filter(registered_app=app, name__in=names).only(
            "id", "name", "previewed_environment_id"
        )
    )
    env_ids = [e.pk for e in envs]
    branch_by_env_id: dict[int, str] = {}
    for env_id, branch in (
        PreviewEnvironment.all_objects.filter(app_environment_id__in=env_ids)
        .order_by("-created_at", "-pk")
        .values_list("app_environment_id", "branch")
    ):
        branch_by_env_id.setdefault(env_id, branch or "")
    out: dict[str, str] = {}
    for e in envs:
        if e.pk in branch_by_env_id:
            out[e.name] = branch_by_env_id[e.pk]
        elif e.previewed_environment_id is not None:
            out[e.name] = ""
    return out


def literal_keys_pending_deploy(app) -> frozenset[str]:
    """Staged literal keys whose value differs from what would actually
    deploy right now (#1923).

    Empty when the app doesn't require secret approval -- every staged
    change is deployable there, so nothing is "pending". Otherwise this is
    ``_deployable_literals``'s own ``unapproved`` computation, surfaced
    instead of only logged: a key lands here when its staged value has no
    matching applied proposal, whether because it is mid-review under an
    always-on approval policy, or because it was staged while approval was
    briefly off and nothing ever proposed it -- either way the next deploy
    reverts it to ``manifest_raw``, which for an already-live key can be an
    outage-causing surprise, not just a missing feature (#1923: "surface it
    in the UI (list the keys that will revert) before deploy").
    """
    if not app.requires_secret_approval:
        return frozenset()
    staged_text = app.manifest_raw_staged or app.manifest_raw or ""
    staged = read_app_env(staged_text)
    if not staged:
        return frozenset()
    deployable = _deployable_literals(app)
    return frozenset(key for key, value in staged.items() if deployable.get(key) != value)
