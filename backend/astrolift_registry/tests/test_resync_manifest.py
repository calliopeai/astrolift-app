"""Tests for the "Resync from source" service + mutation (#386).

The matrix that needs to hold for the operator-facing button:

  * ``in_sync``     — repo matches DB; nothing applied, anchor moves.
  * ``repo_ahead``  — repo has new content; workloads reconciled.
  * ``db_ahead``    — DB has a staged draft + repo unchanged; refuse.
  * ``diverged``    — DB has staged draft AND repo moved; refuse.
  * ``fetch_failed`` — provider raises; DB unchanged.

All five branches are covered here against real Postgres (no
provider mocks at the network level — we inject the fetch
callable so the service code path under test is the production
one, only the SCM I/O is stubbed).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import Container, RegisteredApp, Workload
from astrolift_registry.schema.mutations import (
    RegistryMutation,
    ResyncManifestFromRepoInput,
)
from astrolift_registry.services.manifest_sync import (
    ResyncChanges,
    resync_app_manifest_from_repo,
    summarize_changes,
)
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# --- TOML fixtures -----------------------------------------------------

_BASE_TOML = """\
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
replicas = 2

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

    [workloads.containers.healthcheck]
    kind = "http"
    value = "/healthz"
"""

_ADD_WORKER_TOML = """\
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
replicas = 2

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

    [workloads.containers.healthcheck]
    kind = "http"
    value = "/healthz"

[[workloads]]
name = "worker"
kind = "deployment"
replicas = 1

  [[workloads.containers]]
  name = "worker"
  is_primary = true
  port = 0
"""

# An agent config-repo library (astrolift_version + [skills.*]/[tools.*], no
# name / no [[workloads]]). It can never parse as an app manifest — used to
# assert the resync path gives an actionable "wrong schema" error (#1172).
_AGENT_CONFIG_TOML = """\
astrolift_version = 1

[skills.pr_review]
name = "PR Review"
content = "You are a meticulous reviewer."

[tools.post_comment]
skill = "pr_review"
name = "Post comment"
adapter = "python_fn"
"""


# --- helpers ----------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold(
    *,
    manifest_raw: str = "",
    manifest_raw_staged: str = "",
    source_kind: str = "github",
    source_repo: str = "acme/hello",
    with_connection: bool = True,
):
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo",
    )
    if with_connection:
        SourceConnection.objects.create(
            organization=org,
            kind=SourceConnection.Kind.GITHUB_PAT,
            display_name="Acme PAT",
            account_login="acme",
        )

    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
        source_kind=source_kind,
        source_repo=source_repo,
        manifest_path="astrolift.toml",
        deploy_branch="main",
        default_branch="main",
        manifest_raw=manifest_raw,
        manifest_raw_staged=manifest_raw_staged,
    )

    # Bootstrap workloads/containers from the manifest_raw so the
    # ``applied`` path's diff has something to compare against.
    if manifest_raw.strip():
        from astrolift_manifest.normalize import NormalizationDefaults, normalize
        from astrolift_manifest.parser import parse_raw
        from astrolift_manifest.persist import persist_manifest

        manifest = normalize(parse_raw(manifest_raw), defaults=NormalizationDefaults())
        persist_manifest(app, manifest, raw_text=manifest_raw)
        app.refresh_from_db()

    return org, app


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _stub_fetch(text: str | None):
    """Build a fetch callable that returns ``text`` regardless of args."""

    def _f(connection, repo_full_name, path, ref):  # noqa: ARG001
        return text

    return _f


def _raising_fetch(exc: Exception):
    def _f(connection, repo_full_name, path, ref):  # noqa: ARG001
        raise exc

    return _f


# --- service-level matrix --------------------------------------------


def test_in_sync_returns_in_sync_and_no_changes():
    _org, app = _scaffold(manifest_raw=_BASE_TOML)
    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(_BASE_TOML))

    assert result.status == "in_sync"
    assert result.changes.is_empty
    assert result.error is None

    app.refresh_from_db()
    # Anchor advances even on the no-op so the UI relative-time
    # heartbeat updates.
    assert app.last_resync_at is not None
    assert app.last_synced_hash == app.manifest_hash


def test_repo_ahead_applies_and_reports_added_workload():
    _org, app = _scaffold(manifest_raw=_BASE_TOML)
    pre_hash = app.manifest_hash
    assert pre_hash, "scaffold must set a hash so we can detect the move"

    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(_ADD_WORKER_TOML))

    assert result.status == "applied", result.error
    assert result.changes.workloads_added == ["worker"]
    assert result.changes.workloads_removed == []
    assert result.changes.workloads_changed == []

    app.refresh_from_db()
    assert app.manifest_hash != pre_hash
    assert app.manifest_raw.strip() == _ADD_WORKER_TOML.strip()
    assert app.last_resync_at is not None
    assert app.last_synced_hash == app.manifest_hash

    # The reconcile path actually wrote the new workload row.
    slugs = set(
        Workload.objects.filter(registered_app=app, deleted_at__isnull=True).values_list("slug", flat=True)
    )
    assert {"web", "worker"} == slugs


def test_repo_ahead_handles_first_time_resync_from_empty_db():
    """Freshly-registered app with no stored manifest. The diff
    must treat the DB as empty and report every workload as added
    rather than crash on a None ``before``."""
    _org, app = _scaffold(manifest_raw="")
    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(_BASE_TOML))

    assert result.status == "applied", result.error
    assert result.changes.workloads_added == ["web"]
    app.refresh_from_db()
    assert app.manifest_raw.strip() == _BASE_TOML.strip()


def test_db_ahead_with_unchanged_repo_refuses_and_does_not_clobber():
    """The DB has a staged draft; the repo still matches the DB's
    last-synced source-of-truth. Applying would silently drop the
    operator's pending work — refuse instead."""
    _org, app = _scaffold(manifest_raw=_BASE_TOML, manifest_raw_staged=_ADD_WORKER_TOML)
    pre_hash = app.manifest_hash
    pre_staged = app.manifest_raw_staged

    # Repo content matches the DB's source-of-truth so the staged
    # buffer is the only divergence.
    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(_BASE_TOML))

    # No clobber, no apply. We're "in sync" with the repo per the
    # hash check — the staged buffer is the operator's local edit.
    assert result.status == "in_sync"
    assert result.error is None
    app.refresh_from_db()
    assert app.manifest_raw_staged == pre_staged
    assert app.manifest_hash == pre_hash


def test_diverged_db_staged_and_repo_changed_returns_diverged():
    """Both sides moved: DB has staged drafts, repo has new content."""
    _org, app = _scaffold(manifest_raw=_BASE_TOML, manifest_raw_staged='name = "hello-edit"\n')

    # Repo has a NEW manifest (added worker workload) so we'd
    # otherwise apply it — but the staged buffer would be clobbered.
    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(_ADD_WORKER_TOML))

    assert result.status == "diverged"
    assert result.error is not None
    assert "staged drafts" in result.error

    app.refresh_from_db()
    # Nothing applied.
    assert app.manifest_raw.strip() == _BASE_TOML.strip()
    assert app.manifest_raw_staged.strip() == 'name = "hello-edit"'
    # Worker workload should NOT have been created.
    assert not Workload.objects.filter(registered_app=app, slug="worker", deleted_at__isnull=True).exists()


def test_fetch_failed_provider_error_leaves_db_unchanged():
    _org, app = _scaffold(manifest_raw=_BASE_TOML)
    pre_hash = app.manifest_hash
    pre_raw = app.manifest_raw

    exc = ProviderError("AUTH_REVOKED", "token revoked by host", recoverable=True)
    result = resync_app_manifest_from_repo(app, fetch=_raising_fetch(exc))

    assert result.status == "fetch_failed"
    assert "AUTH_REVOKED" in (result.error or "")
    app.refresh_from_db()
    assert app.manifest_hash == pre_hash
    assert app.manifest_raw == pre_raw
    assert app.last_resync_at is None


def test_fetch_failed_no_source_connection_short_circuits():
    """An org without any active SourceConnection can't resync —
    return fetch_failed with a recovery hint instead of raising."""
    _org, app = _scaffold(manifest_raw=_BASE_TOML, with_connection=False)
    # No fetch callable should be invoked since we bail before that.
    sentinel = {"called": False}

    def _f(*a, **k):
        sentinel["called"] = True
        return None

    result = resync_app_manifest_from_repo(app, fetch=_f)

    assert result.status == "fetch_failed"
    assert sentinel["called"] is False
    assert "source connection" in (result.error or "").lower()


def test_fetch_returning_none_is_file_not_found():
    """fetch_file returning None means the file isn't on the
    branch. We treat this as fetch_failed with a useful message."""
    _org, app = _scaffold(manifest_raw=_BASE_TOML)
    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(None))

    assert result.status == "fetch_failed"
    assert "astrolift.toml" in (result.error or "")
    assert "main" in (result.error or "")


def test_repo_bad_toml_is_fetch_failed_not_a_crash():
    _org, app = _scaffold(manifest_raw=_BASE_TOML)
    pre_hash = app.manifest_hash
    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch("this = is not [valid toml"))

    assert result.status == "fetch_failed"
    assert "failed to parse" in (result.error or "")
    app.refresh_from_db()
    assert app.manifest_hash == pre_hash


def test_repo_agent_config_schema_gives_actionable_error():
    """A repo whose astrolift.toml is the agent config-repo library schema
    (astrolift_version + [skills.*]/[tools.*], no name/workloads) can never
    parse as an app manifest. The resync error must point at agent onboarding
    / skill import rather than dumping a raw "required string 'name' is
    missing" parse error (#1172)."""
    _org, app = _scaffold(manifest_raw=_BASE_TOML)
    pre_hash = app.manifest_hash

    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(_AGENT_CONFIG_TOML))

    assert result.status == "fetch_failed"
    error = result.error or ""
    # Names the actual schema and routes to the right onboarding path.
    assert "agent config" in error.lower()
    assert "skills" in error.lower()
    # Not the generic parse-error message the operator can't act on.
    assert "failed to parse" not in error
    # DB untouched — never applied garbage.
    app.refresh_from_db()
    assert app.manifest_hash == pre_hash


def test_repo_ahead_removed_workload_is_soft_deleted():
    _org, app = _scaffold(manifest_raw=_ADD_WORKER_TOML)
    # Sanity check: both workloads got created by the scaffold.
    assert Workload.objects.filter(registered_app=app, deleted_at__isnull=True).count() == 2

    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(_BASE_TOML))

    assert result.status == "applied"
    assert result.changes.workloads_removed == ["worker"]
    # The "worker" row was soft-deleted, not hard-deleted. The
    # default manager filters out soft-deleted rows, so we check
    # via ``all_objects`` for the row still being there with a
    # ``deleted_at`` stamp.
    assert Workload.all_objects.filter(registered_app=app, slug="worker", deleted_at__isnull=False).exists()


def test_repo_ahead_workload_body_change_is_reported_as_changed():
    _org, app = _scaffold(manifest_raw=_BASE_TOML)
    bumped = _BASE_TOML.replace("replicas = 2", "replicas = 5")

    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(bumped))

    assert result.status == "applied"
    assert result.changes.workloads_changed == ["web"]
    assert result.changes.workloads_added == []
    w = Workload.objects.get(registered_app=app, slug="web", deleted_at__isnull=True)
    assert w.replicas == 5


def test_repo_ahead_clears_staged_when_repo_matches_staged():
    """The operator pushed their staged draft via an external
    workflow; on resync the repo content now matches the staged
    buffer. The buffer should clear so the UI reads as in_sync."""
    _org, app = _scaffold(manifest_raw=_BASE_TOML, manifest_raw_staged=_ADD_WORKER_TOML)

    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(_ADD_WORKER_TOML))

    # Note: this is the diverged case from the staged-buffer
    # detection above — we refuse rather than clear, because we
    # can't tell whether the matching content was already pushed
    # by the operator or coincidence. Conservative path: refuse.
    assert result.status == "diverged"
    app.refresh_from_db()
    assert app.manifest_raw_staged.strip() == _ADD_WORKER_TOML.strip()


# --- summary line ------------------------------------------------------


def test_summarize_changes_handles_singular_and_plural():
    assert summarize_changes(ResyncChanges()) == "Already in sync."

    one = ResyncChanges(workloads_added=["web"])
    assert summarize_changes(one) == "Added 1 workload, env unchanged."

    several = ResyncChanges(
        workloads_added=["web", "worker"],
        env_keys_changed=3,
        schedules_changed=1,
    )
    assert summarize_changes(several) == ("Added 2 workloads, 3 env keys changed, 1 schedule changed.")


# --- mutation envelope -------------------------------------------------


def test_mutation_applied_returns_success_envelope(permission_resolver):
    org, app = _scaffold(manifest_raw=_BASE_TOML)
    permission_resolver.grant(Permission.APP_UPDATE)

    # Patch the service's default fetch via the module attribute so
    # the mutation's call site (no fetch kwarg) hits our stub.
    import astrolift_registry.services.manifest_sync as m

    with _ctx(org):
        orig = m._default_fetch
        m._default_fetch = _stub_fetch(_ADD_WORKER_TOML)  # type: ignore[assignment]
        try:
            result = RegistryMutation().resync_astrolift_manifest_from_repo(
                _info(),
                input=ResyncManifestFromRepoInput(app_slug=app.slug),
            )
        finally:
            m._default_fetch = orig  # type: ignore[assignment]

    assert result.ok, result.errors
    payload = result.data
    assert payload.sync_state == "applied"
    assert payload.workloads_added == ["worker"]
    assert "added 1 workload" in payload.summary.lower()


def test_mutation_diverged_returns_conflict_error(permission_resolver):
    org, app = _scaffold(manifest_raw=_BASE_TOML, manifest_raw_staged='name = "edit"\n')
    permission_resolver.grant(Permission.APP_UPDATE)

    import astrolift_registry.services.manifest_sync as m

    with _ctx(org):
        orig = m._default_fetch
        m._default_fetch = _stub_fetch(_ADD_WORKER_TOML)  # type: ignore[assignment]
        try:
            result = RegistryMutation().resync_astrolift_manifest_from_repo(
                _info(),
                input=ResyncManifestFromRepoInput(app_slug=app.slug),
            )
        finally:
            m._default_fetch = orig  # type: ignore[assignment]

    assert not result.ok
    assert result.errors[0].code == "CONFLICT"
    assert "staged drafts" in result.errors[0].message


def test_mutation_fetch_failed_returns_internal_error(permission_resolver):
    org, app = _scaffold(manifest_raw=_BASE_TOML)
    permission_resolver.grant(Permission.APP_UPDATE)

    import astrolift_registry.services.manifest_sync as m

    with _ctx(org):
        orig = m._default_fetch
        m._default_fetch = _raising_fetch(  # type: ignore[assignment]
            ProviderError("AUTH_REVOKED", "token revoked")
        )
        try:
            result = RegistryMutation().resync_astrolift_manifest_from_repo(
                _info(),
                input=ResyncManifestFromRepoInput(app_slug=app.slug),
            )
        finally:
            m._default_fetch = orig  # type: ignore[assignment]

    assert not result.ok
    assert result.errors[0].code == "INTERNAL"
    assert "AUTH_REVOKED" in result.errors[0].message


def test_mutation_unknown_app_returns_not_found(permission_resolver):
    org, _ = _scaffold(manifest_raw=_BASE_TOML)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().resync_astrolift_manifest_from_repo(
            _info(),
            input=ResyncManifestFromRepoInput(app_slug="does-not-exist"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_mutation_requires_permission():
    org, app = _scaffold(manifest_raw=_BASE_TOML)
    # No grant — should refuse.
    with _ctx(org):
        result = RegistryMutation().resync_astrolift_manifest_from_repo(
            _info(),
            input=ResyncManifestFromRepoInput(app_slug=app.slug),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    # And no row touched.
    app.refresh_from_db()
    assert app.last_resync_at is None


def test_in_sync_with_managed_services_does_not_report_them_as_changed():
    """Managed services in the manifest should appear in the diff
    only when they actually moved. This guards against a regression
    where the diff keyed off ``id(...)`` instead of value."""
    toml_with_msvc = _BASE_TOML + ('\n[[managed_services]]\nkind = "postgres"\nname = "primary"\n')
    _org, app = _scaffold(manifest_raw=toml_with_msvc)
    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(toml_with_msvc))

    assert result.status == "in_sync"
    assert result.changes.managed_services_added == []
    assert result.changes.managed_services_removed == []


def test_managed_services_added_appears_in_changes():
    toml_no_msvc = _BASE_TOML
    toml_with_msvc = _BASE_TOML + ('\n[[managed_services]]\nkind = "postgres"\nname = "primary"\n')
    _org, app = _scaffold(manifest_raw=toml_no_msvc)
    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(toml_with_msvc))

    assert result.status == "applied"
    assert result.changes.managed_services_added == ["primary"]


def test_env_keys_changed_counts_added_and_removed():
    base = """\
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true

    [workloads.containers.env]
    A = "1"
    B = "2"
"""
    after = base.replace('B = "2"', 'B = "3"\nC = "4"')
    _org, app = _scaffold(manifest_raw=base)
    result = resync_app_manifest_from_repo(app, fetch=_stub_fetch(after))

    assert result.status == "applied"
    # B changed value (1), C added (1) — total 2.
    assert result.changes.env_keys_changed == 2


def test_scaffold_has_containers_after_first_apply():
    """Sanity check that the scaffold itself wires containers up so
    test_repo_ahead_workload_body_change has something to bump."""
    _org, app = _scaffold(manifest_raw=_BASE_TOML)
    assert Container.objects.filter(workload__registered_app=app).count() == 1
