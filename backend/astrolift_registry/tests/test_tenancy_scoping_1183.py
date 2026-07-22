"""Cross-tenant scoping regression tests for astrolift_registry (#1183).

Every by-guid / by-slug resolver here must constrain the lookup to the
caller's org. ``@tenant_scoped`` only asserts a tenant exists (it does not
filter) and ``@require_permission`` checks the caller's role in their OWN
org, not row ownership — so a fetch keyed only by guid/slug crosses tenants.

Pins the fail-closed ``NOT_FOUND`` (and no side effect) for a sibling-org
caller, plus the owning-org happy path, for the distinct shapes:

* ``register_app`` — a foreign ``project_id`` must not let a caller create
  an app inside another org's project.
* ``tear_down_app`` — RegisteredApp destroy; the teardown workflow must not
  fire for a cross-org caller.
* ``update_astrolift_security_policy`` — the supply-chain gate the promote
  workflow enforces must not be tamperable across orgs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    RegisterAppInput,
    RegistryMutation,
    TearDownAppInput,
    UpdateSecurityPolicyInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _org(name, slug):
    org = Organization.objects.create(name=name, slug=slug)
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{slug}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{slug}")
    return org, team, project


def _app(org, team, project, *, slug="hello-app"):
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=slug,
        slug=slug,
        provisioning_status="ready",
        subdomain=slug,
    )


# ---------------------------------------------------------------------------
# register_app — foreign project_id must not be usable
# ---------------------------------------------------------------------------


def test_register_app_foreign_project_not_found(permission_resolver, seed_cluster):
    """A caller in org A passing org B's project GUID must get NOT_FOUND —
    otherwise they could create apps inside a project they don't own (the
    new app's organization is derived from ``project.organization``)."""
    org_a, _, _ = _org("Acme", "acme-1183")
    org_b, _, project_b = _org("Globex", "globex-1183")
    # Give the caller's own org a managed cluster so a *missing* cluster
    # can't be what trips the guard — we want the project org check to fire.
    seed_cluster(org_a, slug="acme-1183")
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org_a):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project_b.guid),
                name="Intruder",
                slug="intruder",
                source_repo="acme/intruder",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "projectId"
    # Nothing created in either org.
    assert not RegisteredApp.objects.filter(slug="intruder").exists()


def test_register_app_own_project_works(permission_resolver, seed_cluster):
    org_a, _, project_a = _org("Acme", "acme-ok-1183")
    seed_cluster(org_a, slug="acme-ok-1183")
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org_a):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project_a.guid),
                name="Mine",
                slug="mine-1183",
                source_repo="acme/mine",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="mine-1183")
    assert app.organization_id == org_a.id


# ---------------------------------------------------------------------------
# tear_down_app — RegisteredApp destroy (workflow side effect)
# ---------------------------------------------------------------------------


def test_tear_down_app_cross_org_not_found_and_no_workflow(permission_resolver, monkeypatch):
    starts: list[str] = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, args, *, workflow_id, task_queue=None: starts.append(name),
    )

    org_a, team_a, project_a = _org("Owner", "owner-td-1183")
    app = _app(org_a, team_a, project_a, slug="teardown-target")
    org_b, _, _ = _org("Sibling", "sib-td-1183")
    permission_resolver.grant(Permission.APP_DELETE)

    with _ctx(org_b):
        result = RegistryMutation().tear_down_app(_info(), input=TearDownAppInput(id=str(app.guid)))

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert starts == [], "teardown workflow started for a cross-org caller"
    app.refresh_from_db()
    assert app.deleted_at is None


def test_tear_down_app_same_org_starts_workflow(permission_resolver, monkeypatch):
    starts: list[str] = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, args, *, workflow_id, task_queue=None: starts.append(name),
    )

    org_a, team_a, project_a = _org("Owner", "owner-td-ok-1183")
    app = _app(org_a, team_a, project_a, slug="teardown-ok")
    permission_resolver.grant(Permission.APP_DELETE)

    with _ctx(org_a):
        result = RegistryMutation().tear_down_app(_info(), input=TearDownAppInput(id=str(app.guid)))

    assert result.ok, result.errors
    assert starts == ["TearDownAppWorkflow"]


# ---------------------------------------------------------------------------
# update_astrolift_security_policy — the gate must not be tamperable
# ---------------------------------------------------------------------------


def test_update_security_policy_cross_org_not_found_and_untouched(permission_resolver):
    """A sibling-org caller must not loosen (or touch) another org's
    supply-chain gate — the one the promote workflow reads to block a
    vulnerable image."""
    org_a, team_a, project_a = _org("Owner", "owner-sp-1183")
    app = _app(org_a, team_a, project_a, slug="policy-target")
    app.security_policy = {
        "block_on_critical_cves": True,
        "block_on_missing_signature": True,
        "block_on_high_cve_threshold": 1,
    }
    app.save(update_fields=["security_policy", "updated_at", "version"])

    org_b, _, _ = _org("Sibling", "sib-sp-1183")
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org_b):
        result = RegistryMutation().update_astrolift_security_policy(
            _info(),
            input=UpdateSecurityPolicyInput(
                app_slug=app.slug,
                block_on_critical_cves=False,
                block_on_missing_signature=False,
                block_on_high_cve_threshold=None,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    app.refresh_from_db()
    # Gate untouched — still blocking.
    assert app.security_policy["block_on_critical_cves"] is True
    assert app.security_policy["block_on_missing_signature"] is True
    assert app.security_policy["block_on_high_cve_threshold"] == 1


def test_update_security_policy_same_org_works(permission_resolver):
    org_a, team_a, project_a = _org("Owner", "owner-sp-ok-1183")
    app = _app(org_a, team_a, project_a, slug="policy-ok")
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org_a):
        result = RegistryMutation().update_astrolift_security_policy(
            _info(),
            input=UpdateSecurityPolicyInput(
                app_slug=app.slug,
                block_on_critical_cves=False,
                block_on_missing_signature=True,
                block_on_high_cve_threshold=7,
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.security_policy["block_on_high_cve_threshold"] == 7
