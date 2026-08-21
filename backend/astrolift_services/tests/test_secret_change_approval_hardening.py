"""Hardening tests for the secret-change approval lifecycle (#488).

Verify-then-harden pass over the ``/approvals`` (secret-proposals half)
surface. The existing ``test_secret_change_proposals.py`` covers the
propose/approve happy paths and the self-approve / reason / eligibility
gates; this file fills the gaps that a QA pass flagged:

* **Apply-on-approve for every op** — the ``secret_change_apply`` step
  (delete / attach_bundle / detach_bundle) was only exercised for
  ``set``.  These prove the underlying state actually changes when
  quorum is reached.
* **Cross-tenant isolation** — the approve / reject / withdraw
  mutations + the list / detail queries must never touch another org's
  proposal.  This is the repo's historically weak spot (#1042 / #1183).
* **Invalid transitions** — approve / reject / withdraw against a
  non-``pending`` proposal must return a PRECONDITION envelope (never
  raise), and the model state machine must refuse illegal flips.
* **Apply-failure bookkeeping** — a failed apply leaves the proposal
  ``approved`` with ``apply_error`` populated, not ``applied``.
* **Cross-org same-slug bundle** — regression for the apply-time bundle
  re-resolution dropping the org scope (SecretBundle.slug is unique
  only per (team, slug), so an unscoped lookup can grab another org's
  bundle).
* **reject / withdraw permission + eligibility gates** — mirrors the
  approve-side gate tests that already exist.
* **Idempotent double-approve** — a second vote from the same approver
  must not double-count toward quorum.

Convention: call resolver methods directly (not ``schema.execute_sync``)
with a ``SimpleNamespace`` info shape inside a ``tenant_context``, with
the ``permission_resolver`` fixture granting the needed permissions —
matching ``test_secret_change_proposals.py``.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    AppSecretBundleRef,
    SecretBundle,
    SecretChangeApproval,
    SecretChangeProposal,
)
from astrolift_services.schema.mutations import (
    ApproveSecretChangeInput,
    RejectSecretChangeInput,
    ServicesMutation,
    WithdrawSecretChangeInput,
)
from astrolift_services.schema.queries import ServicesQuery
from core.decorators import TenantRequired
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


_BASE_TOML = """\
astrolift_version = 1
name = "hello"

[env]
KEEP_ME = "yes"

[[workloads]]
name = "web"
kind = "deployment"
"""


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _make_user(username: str):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _scaffold(
    suffix: str = "a",
    *,
    requires_secret_approval: bool = True,
    min_approvals: int = 1,
    bundle_slug: str = "prod-secrets",
):
    """Build an independent (org, app, env, bundle) tenant.

    ``suffix`` disambiguates every org-unique slug so two tenants can
    coexist in one test DB.  ``bundle_slug`` is deliberately NOT
    suffixed by default so callers can force a same-slug collision
    across orgs (bundle uniqueness is only (team, slug)).
    """
    org = Organization.objects.create(name=f"Org {suffix}", slug=f"org-{suffix}")
    team = Team.objects.create(organization=org, name=f"Eng {suffix}", slug=f"eng-{suffix}")
    project = Project.objects.create(
        organization=org,
        team=team,
        name=f"Demo {suffix}",
        slug=f"demo-{suffix}",
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s Native",
                slug="k8s-native",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="k8s-native")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"local-{suffix}",
        name=f"Local {suffix}",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=f"Hello {suffix}",
        slug=f"hello-{suffix}",
        provisioning_status="ready",
        manifest_raw=_BASE_TOML,
        requires_secret_approval=requires_secret_approval,
        secret_minimum_approvals=min_approvals,
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    bundle = SecretBundle.objects.create(
        organization=org,
        team=team,
        slug=bundle_slug,
        name=f"Bundle {suffix}",
        backend_ref=f"vault:/{suffix}/prod",
    )
    return org, app, env, bundle


def _ctx(org, *, actor=None):
    return tenant_context(
        TenantContext(
            organization_id=org.id,
            actor_user_id=actor.pk if actor else None,
        )
    )


def _make_proposal(app, *, proposer, op="set", payload=None, env_name="", status=None):
    payload = payload if payload is not None else {"key": "API_KEY", "value": "abc"}
    proposal = SecretChangeProposal.objects.create(
        registered_app=app,
        environment_name=env_name,
        proposer=proposer,
        op=op,
        payload=payload,
        payload_diff={"op": op, "summary": "test"},
        required_approver_count=app.secret_minimum_approvals or 1,
        expires_at=timezone.now() + timedelta(days=7),
        created_by=proposer,
        updated_by=proposer,
    )
    if status is not None:
        # Drive straight to a terminal/non-pending state for
        # invalid-transition tests without going through a mutation.
        proposal.status = status
        proposal.save(update_fields=["status", "updated_at", "version"])
    return proposal


# ---------------------------------------------------------------------
# A. Apply-on-approve for each op (lifts secret_change_apply coverage)


def test_approve_applies_delete_removes_key(permission_resolver):
    org, app, _, _ = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("del-proposer")
    approver = _make_user("del-approver")
    proposal = _make_proposal(
        app,
        proposer=proposer,
        op="delete",
        payload={"key": "KEEP_ME"},
    )

    with _ctx(org, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert result.ok, result.errors
    proposal.refresh_from_db()
    assert proposal.status == "applied"
    assert proposal.applied_at is not None
    app.refresh_from_db()
    # The delete was staged: KEEP_ME is gone from the staged manifest.
    assert "KEEP_ME" not in read_app_env(app.manifest_raw_staged or "")


def test_approve_applies_attach_bundle_creates_ref(permission_resolver):
    org, app, env, bundle = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("att-proposer")
    approver = _make_user("att-approver")
    proposal = _make_proposal(
        app,
        proposer=proposer,
        op="attach_bundle",
        payload={"bundle_slug": bundle.slug, "prefix": "STRIPE_"},
        env_name=env.name,
    )

    with _ctx(org, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert result.ok, result.errors
    proposal.refresh_from_db()
    assert proposal.status == "applied"
    ref = AppSecretBundleRef.objects.get(
        registered_app=app,
        app_environment=env,
        secret_bundle=bundle,
        deleted_at__isnull=True,
    )
    assert ref.prefix == "STRIPE_"


def test_approve_applies_detach_bundle_soft_deletes_ref(permission_resolver):
    org, app, env, bundle = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("det-proposer")
    approver = _make_user("det-approver")
    # Seed a live attachment directly (avoid the gated attach mutation).
    ref = AppSecretBundleRef.objects.create(
        registered_app=app,
        app_environment=env,
        secret_bundle=bundle,
        prefix="",
        created_by=proposer,
        updated_by=proposer,
    )
    proposal = _make_proposal(
        app,
        proposer=proposer,
        op="detach_bundle",
        payload={"attachment_id": str(ref.guid)},
        env_name=env.name,
    )

    with _ctx(org, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert result.ok, result.errors
    proposal.refresh_from_db()
    assert proposal.status == "applied"
    ref.refresh_from_db()
    assert ref.deleted_at is not None


@pytest.mark.parametrize("seeded_prefix,proposed_prefix", [("OLD_", "NEW_"), ("SAME_", "SAME_")])
def test_approve_attach_existing_ref_is_idempotent(permission_resolver, seeded_prefix, proposed_prefix):
    """Apply is re-runnable: when the (app, env, bundle) attachment
    already exists, approving an attach proposal updates the prefix in
    place (or no-ops when unchanged) rather than creating a duplicate
    row."""
    org, app, env, bundle = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user(f"ae-proposer-{seeded_prefix}")
    approver = _make_user(f"ae-approver-{seeded_prefix}")
    AppSecretBundleRef.objects.create(
        registered_app=app,
        app_environment=env,
        secret_bundle=bundle,
        prefix=seeded_prefix,
        created_by=proposer,
        updated_by=proposer,
    )
    proposal = _make_proposal(
        app,
        proposer=proposer,
        op="attach_bundle",
        payload={"bundle_slug": bundle.slug, "prefix": proposed_prefix},
        env_name=env.name,
    )

    with _ctx(org, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert result.ok, result.errors
    proposal.refresh_from_db()
    assert proposal.status == "applied"
    refs = AppSecretBundleRef.objects.filter(
        registered_app=app,
        app_environment=env,
        secret_bundle=bundle,
        deleted_at__isnull=True,
    )
    assert refs.count() == 1  # updated in place, never duplicated
    assert refs.first().prefix == proposed_prefix


def test_approve_delete_absent_key_is_idempotent_success(permission_resolver):
    """A delete proposal for a key that vanished between propose and
    approve applies cleanly (idempotent) — the proposal reaches
    ``applied`` and the manifest is untouched."""
    org, app, _, _ = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("da-proposer")
    approver = _make_user("da-approver")
    proposal = _make_proposal(
        app,
        proposer=proposer,
        op="delete",
        payload={"key": "GHOST"},  # not present in _BASE_TOML [env]
    )
    before = app.manifest_raw

    with _ctx(org, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert result.ok, result.errors
    proposal.refresh_from_db()
    assert proposal.status == "applied"
    app.refresh_from_db()
    # Nothing to remove → staging buffer stays empty, source unchanged.
    assert app.manifest_raw == before
    assert app.manifest_raw_staged == ""


# ---------------------------------------------------------------------
# B. Cross-tenant isolation — approve / reject / withdraw + queries


def test_approve_cross_tenant_denied(permission_resolver):
    org_a, app_a, _, _ = _scaffold(suffix="a", min_approvals=1)
    org_b, _, _, _ = _scaffold(suffix="b", min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("xt-proposer")
    intruder = _make_user("xt-intruder")
    proposal = _make_proposal(app_a, proposer=proposer)

    # Intruder is a fully-permissioned approver in org B, acting under
    # org B's tenant context, reaching for org A's proposal by guid.
    with _ctx(org_b, actor=intruder):
        result = ServicesMutation().approve_secret_change(
            _info(user=intruder),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    proposal.refresh_from_db()
    assert proposal.status == "pending"  # untouched
    assert SecretChangeApproval.objects.filter(proposal=proposal).count() == 0


def test_reject_cross_tenant_denied(permission_resolver):
    org_a, app_a, _, _ = _scaffold(suffix="a", min_approvals=1)
    org_b, _, _, _ = _scaffold(suffix="b", min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("xtr-proposer")
    intruder = _make_user("xtr-intruder")
    proposal = _make_proposal(app_a, proposer=proposer)

    with _ctx(org_b, actor=intruder):
        result = ServicesMutation().reject_secret_change(
            _info(user=intruder),
            input=RejectSecretChangeInput(proposal_id=str(proposal.guid), reason="nope"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    proposal.refresh_from_db()
    assert proposal.status == "pending"


def test_withdraw_cross_tenant_denied(permission_resolver):
    org_a, app_a, _, _ = _scaffold(suffix="a", min_approvals=1)
    org_b, _, _, _ = _scaffold(suffix="b", min_approvals=1)
    permission_resolver.grant(Permission.APP_UPDATE)
    proposer = _make_user("xtw-proposer")
    proposal = _make_proposal(app_a, proposer=proposer)

    # Even the *same user id* acting in org B can't reach org A's row.
    with _ctx(org_b, actor=proposer):
        result = ServicesMutation().withdraw_secret_change(
            _info(user=proposer),
            input=WithdrawSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    proposal.refresh_from_db()
    assert proposal.status == "pending"


def test_list_and_detail_queries_scoped_to_tenant(permission_resolver):
    org_a, app_a, _, _ = _scaffold(suffix="a", min_approvals=1)
    org_b, app_b, _, _ = _scaffold(suffix="b", min_approvals=1)
    permission_resolver.grant(Permission.APP_READ)
    proposer = _make_user("q-proposer")
    p_a = _make_proposal(app_a, proposer=proposer, payload={"key": "A_KEY", "value": "v"})
    p_b = _make_proposal(app_b, proposer=proposer, payload={"key": "B_KEY", "value": "v"})

    with _ctx(org_b):
        listed = ServicesQuery().astrolift_secret_change_proposals(_info())
        detail_own = ServicesQuery().astrolift_secret_change_proposal(_info(), id=str(p_b.guid))
        detail_foreign = ServicesQuery().astrolift_secret_change_proposal(_info(), id=str(p_a.guid))

    listed_ids = {p.id for p in listed}
    assert str(p_b.guid) in {str(i) for i in listed_ids}
    assert str(p_a.guid) not in {str(i) for i in listed_ids}
    assert detail_own is not None
    assert detail_foreign is None  # org A's proposal invisible to org B


def test_list_proposals_denies_without_tenant(permission_resolver):
    # A tenant context with no resolved org must NOT fall through to
    # "all rows" (#1042). ``@tenant_scoped()`` is the guard: it raises
    # TenantRequired rather than letting an org-less caller read the
    # global proposal set.
    org_a, app_a, _, _ = _scaffold(suffix="a", min_approvals=1)
    permission_resolver.grant(Permission.APP_READ)
    _make_proposal(app_a, proposer=_make_user("nt-proposer"))

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            ServicesQuery().astrolift_secret_change_proposals(_info())


# ---------------------------------------------------------------------
# C. Cross-org same-slug bundle — regression for the apply-time scope fix


def test_approve_attach_bundle_same_slug_other_org_attaches_own(permission_resolver):
    """Two orgs each own a bundle named ``prod-secrets``. Org A's row is
    created first (lower pk). Approving org B's attach proposal must
    resolve *org B's* bundle at apply time — not fall through to org A's
    same-slug row (which would trip the org-mismatch guard and strand
    the proposal in ``approved``)."""
    org_a, _, _, bundle_a = _scaffold(suffix="a", min_approvals=1, bundle_slug="prod-secrets")
    org_b, app_b, env_b, bundle_b = _scaffold(suffix="b", min_approvals=1, bundle_slug="prod-secrets")
    # Sanity: same slug, different orgs, A created first.
    assert bundle_a.slug == bundle_b.slug
    assert bundle_a.pk < bundle_b.pk

    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("co-proposer")
    approver = _make_user("co-approver")
    proposal = _make_proposal(
        app_b,
        proposer=proposer,
        op="attach_bundle",
        payload={"bundle_slug": "prod-secrets", "prefix": ""},
        env_name=env_b.name,
    )

    with _ctx(org_b, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert result.ok, result.errors
    proposal.refresh_from_db()
    assert proposal.status == "applied", f"apply_error={proposal.apply_error!r}"
    # The attachment points at org B's bundle, not org A's.
    ref = AppSecretBundleRef.objects.get(registered_app=app_b, deleted_at__isnull=True)
    assert ref.secret_bundle_id == bundle_b.pk
    assert not AppSecretBundleRef.objects.filter(secret_bundle=bundle_a).exists()


# ---------------------------------------------------------------------
# D. Invalid transitions — resolver PRECONDITION + model state machine


@pytest.mark.parametrize("terminal_status", ["applied", "rejected", "withdrawn", "expired"])
def test_approve_non_pending_returns_precondition(permission_resolver, terminal_status):
    org, app, _, _ = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user(f"np-proposer-{terminal_status}")
    approver = _make_user(f"np-approver-{terminal_status}")
    proposal = _make_proposal(app, proposer=proposer, status=terminal_status)

    with _ctx(org, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert terminal_status in result.errors[0].message
    proposal.refresh_from_db()
    assert proposal.status == terminal_status  # unchanged


def test_reject_non_pending_returns_precondition(permission_resolver):
    org, app, _, _ = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("rnp-proposer")
    rejecter = _make_user("rnp-rejecter")
    proposal = _make_proposal(app, proposer=proposer, status="withdrawn")

    with _ctx(org, actor=rejecter):
        result = ServicesMutation().reject_secret_change(
            _info(user=rejecter),
            input=RejectSecretChangeInput(proposal_id=str(proposal.guid), reason="too late"),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_withdraw_non_pending_returns_precondition(permission_resolver):
    org, app, _, _ = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.APP_UPDATE)
    proposer = _make_user("wnp-proposer")
    proposal = _make_proposal(app, proposer=proposer, status="rejected")

    with _ctx(org, actor=proposer):
        result = ServicesMutation().withdraw_secret_change(
            _info(user=proposer),
            input=WithdrawSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_model_transition_refuses_illegal_flip():
    """The state machine is the single source of truth — a terminal
    state has no outgoing edges and ``transition_to`` raises rather
    than silently corrupting status (covers the raise guard directly)."""
    org, app, _, _ = _scaffold(min_approvals=1)
    proposer = _make_user("sm-proposer")
    proposal = _make_proposal(app, proposer=proposer, status="rejected")

    with pytest.raises(ValueError, match="cannot transition"):
        proposal.transition_to(SecretChangeProposal.Status.APPROVED)

    # applied is terminal too — no path back to pending.
    proposal.status = SecretChangeProposal.Status.APPLIED.value
    with pytest.raises(ValueError):
        proposal.transition_to(SecretChangeProposal.Status.PENDING)


# ---------------------------------------------------------------------
# E. Apply-failure bookkeeping


def test_approve_apply_failure_keeps_approved_and_records_error(permission_resolver):
    org, app, env, bundle = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("af-proposer")
    approver = _make_user("af-approver")
    proposal = _make_proposal(
        app,
        proposer=proposer,
        op="attach_bundle",
        payload={"bundle_slug": bundle.slug, "prefix": ""},
        env_name=env.name,
    )
    # Soft-delete the bundle out from under the proposal WITHOUT firing
    # the model's cleanup workflow (queryset update bypasses the
    # overridden soft_delete). Apply will fail to resolve the bundle.
    SecretBundle.objects.filter(pk=bundle.pk).update(deleted_at=timezone.now())

    with _ctx(org, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    # The mutation still returns a success envelope for the *vote*; the
    # apply failure is surfaced on the row, not as a raised exception.
    assert result.ok, result.errors
    proposal.refresh_from_db()
    assert proposal.status == "approved"  # reached quorum but did NOT apply
    assert proposal.applied_at is None
    assert proposal.decided_at is not None
    assert "not found" in proposal.apply_error
    assert not AppSecretBundleRef.objects.filter(registered_app=app).exists()


# ---------------------------------------------------------------------
# F. Self-approve allowed when the Constance flag is on


def test_self_approve_allowed_when_flag_on(permission_resolver, monkeypatch):
    org, app, _, _ = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    monkeypatch.setattr(
        "astrolift_services.schema.mutations.secret_changes._self_approve_secrets_allowed",
        lambda: True,
    )
    proposer = _make_user("sa-proposer")
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=proposer):
        result = ServicesMutation().approve_secret_change(
            _info(user=proposer),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert result.ok, result.errors
    proposal.refresh_from_db()
    assert proposal.status == "applied"


# ---------------------------------------------------------------------
# G. reject / withdraw permission + eligibility gates


def test_reject_requires_permission():
    # No permission_resolver fixture → default resolver denies.
    org, app, _, _ = _scaffold(min_approvals=1)
    proposer = _make_user("rp-proposer")
    rejecter = _make_user("rp-rejecter")
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=rejecter):
        result = ServicesMutation().reject_secret_change(
            _info(user=rejecter),
            input=RejectSecretChangeInput(proposal_id=str(proposal.guid), reason="no"),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_reject_requires_eligibility_when_users_set(permission_resolver):
    org, app, _, _ = _scaffold(min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("re-proposer")
    ineligible = _make_user("re-ineligible")
    eligible = _make_user("re-eligible")
    app.secret_approver_users.set([eligible])
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=ineligible):
        denied = ServicesMutation().reject_secret_change(
            _info(user=ineligible),
            input=RejectSecretChangeInput(proposal_id=str(proposal.guid), reason="pii"),
        )
    assert not denied.ok
    assert denied.errors[0].code == "PERMISSION_DENIED"
    proposal.refresh_from_db()
    assert proposal.status == "pending"  # ineligible reject did not land

    with _ctx(org, actor=eligible):
        ok = ServicesMutation().reject_secret_change(
            _info(user=eligible),
            input=RejectSecretChangeInput(proposal_id=str(proposal.guid), reason="pii"),
        )
    assert ok.ok
    proposal.refresh_from_db()
    assert proposal.status == "rejected"


def test_withdraw_requires_permission():
    # Withdraw is gated on APP_UPDATE; default resolver denies.
    org, app, _, _ = _scaffold(min_approvals=1)
    proposer = _make_user("wp-proposer")
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=proposer):
        result = ServicesMutation().withdraw_secret_change(
            _info(user=proposer),
            input=WithdrawSecretChangeInput(proposal_id=str(proposal.guid)),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------
# H. Idempotent double-approve must not double-count toward quorum


def test_double_approve_same_user_does_not_reach_quorum(permission_resolver):
    org, app, _, _ = _scaffold(min_approvals=2)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("dbl-proposer")
    approver = _make_user("dbl-approver")
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=approver):
        first = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )
        second = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert first.ok and second.ok
    proposal.refresh_from_db()
    # One distinct approver, quorum is 2 → still pending, one vote row.
    assert proposal.status == "pending"
    assert (
        SecretChangeApproval.objects.filter(
            proposal=proposal,
            decision="approved",
            deleted_at__isnull=True,
        ).count()
        == 1
    )


# ---------------------------------------------------------------------
# I. #1214 — reject after approve must flip the stale vote row, not skip it


def test_reject_after_approve_flips_stale_vote_row_1214(permission_resolver):
    """An approver who already voted APPROVED then rejects must have their
    row flipped to REJECTED (single row, with the reason) — otherwise the
    denormalized ApproverList shows them as 'approved' on a rejected proposal."""
    org, app, _, _ = _scaffold(min_approvals=2)  # 2 so first approve stays pending
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("flip-proposer")
    approver = _make_user("flip-approver")
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=approver):
        appr = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )
    assert appr.ok
    assert (
        SecretChangeApproval.objects.get(proposal=proposal, approver=approver).decision
        == SecretChangeApproval.Decision.APPROVED.value
    )

    with _ctx(org, actor=approver):
        rej = ServicesMutation().reject_secret_change(
            _info(user=approver),
            input=RejectSecretChangeInput(proposal_id=str(proposal.guid), reason="changed mind"),
        )
    assert rej.ok
    proposal.refresh_from_db()
    assert proposal.status == "rejected"

    rows = SecretChangeApproval.objects.filter(proposal=proposal, approver=approver, deleted_at__isnull=True)
    assert rows.count() == 1  # flipped in place, not a duplicate row
    row = rows.first()
    assert row.decision == SecretChangeApproval.Decision.REJECTED.value
    assert row.reason == "changed mind"
