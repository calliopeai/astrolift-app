"""Tests for the secret-change approval workflow (#488)."""

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
    AttachSecretBundleInput,
    DeleteAppSecretInput,
    DetachSecretBundleInput,
    ProposeSecretChangeInput,
    RejectSecretChangeInput,
    ServicesMutation,
    SetAppSecretInput,
    WithdrawSecretChangeInput,
)
from astrolift_services.schema.queries import ServicesQuery
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
    if user is None:
        request = SimpleNamespace(user=None, META={})
    else:
        request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _make_user(username: str):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _scaffold(*, requires_secret_approval: bool = True, min_approvals: int = 1):
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo",
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
        slug="local",
        name="Local",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
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
        slug="prod-secrets",
        name="Prod Secrets",
        backend_ref="vault:/acme/prod",
    )
    return org, app, env, bundle


def _ctx(org, *, actor=None):
    return tenant_context(
        TenantContext(
            organization_id=org.id,
            actor_user_id=actor.pk if actor else None,
        )
    )


# ---- proposal creation via legacy mutations -----------------------


def test_set_app_secret_creates_proposal_when_gate_on(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True)
    permission_resolver.grant(Permission.APP_UPDATE)
    proposer = _make_user("proposer")

    with _ctx(org, actor=proposer):
        result = ServicesMutation().set_app_secret(
            _info(user=proposer),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="API_KEY",
                value="new-value",
            ),
        )

    assert result.ok, result.errors
    assert result.data.pending_proposal_id is not None
    # Underlying manifest NOT touched — proposal is staged for review.
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""
    assert "API_KEY" not in read_app_env(app.manifest_raw)

    proposals = SecretChangeProposal.objects.filter(registered_app=app, deleted_at__isnull=True)
    assert proposals.count() == 1
    proposal = proposals.first()
    assert proposal.status == "pending"
    assert proposal.op == "set"
    assert proposal.payload == {"key": "API_KEY", "value": "new-value"}
    assert proposal.required_approver_count == 1
    assert proposal.payload_diff["op"] == "set"
    assert proposal.payload_diff["after"]["key"] == "API_KEY"
    assert proposal.expires_at > timezone.now()


def test_set_app_secret_applies_immediately_when_gate_off(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=False)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="API_KEY",
                value="abc",
            ),
        )

    assert result.ok
    assert result.data.pending_proposal_id is None
    app.refresh_from_db()
    assert read_app_env(app.manifest_raw_staged)["API_KEY"] == "abc"
    assert SecretChangeProposal.objects.count() == 0


def test_delete_app_secret_creates_proposal(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True)
    permission_resolver.grant(Permission.APP_UPDATE)
    proposer = _make_user("proposer")

    with _ctx(org, actor=proposer):
        result = ServicesMutation().delete_app_secret(
            _info(user=proposer),
            input=DeleteAppSecretInput(app_slug=app.slug, key="KEEP_ME"),
        )

    assert result.ok
    assert result.data.pending_proposal_id is not None
    # Key still present until approval applies the delete.
    app.refresh_from_db()
    assert "KEEP_ME" in read_app_env(app.manifest_raw)
    proposal = SecretChangeProposal.objects.get(registered_app=app)
    assert proposal.op == "delete"
    assert proposal.payload == {"key": "KEEP_ME"}


def test_delete_app_secret_proposal_rejects_missing_key(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().delete_app_secret(
            _info(),
            input=DeleteAppSecretInput(app_slug=app.slug, key="NOPE"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert SecretChangeProposal.objects.count() == 0


def test_attach_secret_bundle_creates_proposal(permission_resolver):
    org, app, env, bundle = _scaffold(requires_secret_approval=True)
    permission_resolver.grant(Permission.APP_UPDATE)
    proposer = _make_user("proposer")

    with _ctx(org, actor=proposer):
        result = ServicesMutation().attach_secret_bundle(
            _info(user=proposer),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
                prefix="STRIPE_",
            ),
        )

    assert result.ok
    # No live attachment yet.
    assert AppSecretBundleRef.objects.filter(deleted_at__isnull=True).count() == 0
    proposal = SecretChangeProposal.objects.get(registered_app=app)
    assert proposal.op == "attach_bundle"
    assert proposal.payload["bundle_slug"] == bundle.slug
    assert proposal.payload["prefix"] == "STRIPE_"
    assert proposal.environment_name == env.name


def test_detach_secret_bundle_creates_proposal(permission_resolver):
    # Detach gates work only when there's a live attachment; bypass
    # the gate to seed one, then re-enable.
    org, app, env, bundle = _scaffold(requires_secret_approval=False)
    permission_resolver.grant(Permission.APP_UPDATE)
    proposer = _make_user("proposer")

    with _ctx(org, actor=proposer):
        attach = ServicesMutation().attach_secret_bundle(
            _info(user=proposer),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
            ),
        )
    assert attach.ok
    app.requires_secret_approval = True
    app.save(update_fields=["requires_secret_approval", "updated_at", "version"])
    ref = AppSecretBundleRef.objects.get(deleted_at__isnull=True)

    with _ctx(org, actor=proposer):
        result = ServicesMutation().detach_secret_bundle(
            _info(user=proposer),
            input=DetachSecretBundleInput(attachment_id=str(ref.guid)),
        )

    assert result.ok
    assert result.data.pending_proposal_id is not None
    assert not result.data.deleted
    ref.refresh_from_db()
    assert ref.deleted_at is None  # not yet deleted
    proposal = SecretChangeProposal.objects.get(registered_app=app)
    assert proposal.op == "detach_bundle"
    assert proposal.payload == {"attachment_id": str(ref.guid)}


# ---- explicit proposeSecretChange ---------------------------------


def test_propose_secret_change_set_op(permission_resolver):
    # Gate OFF — propose still works as the canonical surface for
    # teams that want the review trail without flipping the gate.
    org, app, _, _ = _scaffold(requires_secret_approval=False)
    permission_resolver.grant(Permission.APP_UPDATE)
    proposer = _make_user("proposer")

    with _ctx(org, actor=proposer):
        result = ServicesMutation().propose_secret_change(
            _info(user=proposer),
            input=ProposeSecretChangeInput(
                app_slug=app.slug,
                op="set",
                key="API_KEY",
                value="zzz",
            ),
        )

    assert result.ok, result.errors
    assert result.data.op == "set"
    assert result.data.status == "pending"
    assert result.data.payload == {"key": "API_KEY", "value": "zzz"}


def test_propose_secret_change_rejects_bad_op(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().propose_secret_change(
            _info(),
            input=ProposeSecretChangeInput(app_slug=app.slug, op="lol"),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "op"


# ---- approve / reject / withdraw ----------------------------------


def _make_proposal(app, *, proposer, op="set", payload=None, env_name=""):
    payload = payload or {"key": "API_KEY", "value": "abc"}
    return SecretChangeProposal.objects.create(
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


def test_approve_with_quorum_one_auto_applies(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=1)
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("proposer")
    approver = _make_user("approver")
    proposal = _make_proposal(app, proposer=proposer)

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
    assert read_app_env(app.manifest_raw_staged)["API_KEY"] == "abc"


def test_partial_approvals_keep_proposal_pending(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=2)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("proposer")
    approver_one = _make_user("a1")
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=approver_one):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver_one),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert result.ok
    proposal.refresh_from_db()
    assert proposal.status == "pending"
    assert SecretChangeApproval.objects.filter(proposal=proposal, decision="approved").count() == 1
    app.refresh_from_db()
    assert "API_KEY" not in (read_app_env(app.manifest_raw_staged or app.manifest_raw))


def test_quorum_two_approvals_applies(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=2)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("proposer")
    a1 = _make_user("a1")
    a2 = _make_user("a2")
    proposal = _make_proposal(app, proposer=proposer)

    for actor in (a1, a2):
        with _ctx(org, actor=actor):
            r = ServicesMutation().approve_secret_change(
                _info(user=actor),
                input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
            )
            assert r.ok, r.errors

    proposal.refresh_from_db()
    assert proposal.status == "applied"


def test_self_approval_blocked_by_default(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("proposer")
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=proposer):
        result = ServicesMutation().approve_secret_change(
            _info(user=proposer),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "your own" in result.errors[0].message


def test_reject_kills_proposal_and_requires_reason(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=2)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("proposer")
    rejecter = _make_user("rejecter")
    proposal = _make_proposal(app, proposer=proposer)

    # No reason → VALIDATION
    with _ctx(org, actor=rejecter):
        no_reason = ServicesMutation().reject_secret_change(
            _info(user=rejecter),
            input=RejectSecretChangeInput(proposal_id=str(proposal.guid), reason=""),
        )
    assert not no_reason.ok
    assert no_reason.errors[0].code == "VALIDATION"

    # With reason → REJECTED
    with _ctx(org, actor=rejecter):
        result = ServicesMutation().reject_secret_change(
            _info(user=rejecter),
            input=RejectSecretChangeInput(proposal_id=str(proposal.guid), reason="leaks PII"),
        )
    assert result.ok
    proposal.refresh_from_db()
    assert proposal.status == "rejected"
    assert proposal.decided_at is not None


def test_proposer_cannot_reject_own_proposal(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("proposer")
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=proposer):
        result = ServicesMutation().reject_secret_change(
            _info(user=proposer),
            input=RejectSecretChangeInput(proposal_id=str(proposal.guid), reason="changed mind"),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "withdraw" in result.errors[0].message.lower()


def test_withdraw_is_proposer_only(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=1)
    permission_resolver.grant(Permission.APP_UPDATE)
    proposer = _make_user("proposer")
    other = _make_user("other")
    proposal = _make_proposal(app, proposer=proposer)

    # Non-proposer is blocked.
    with _ctx(org, actor=other):
        denied = ServicesMutation().withdraw_secret_change(
            _info(user=other),
            input=WithdrawSecretChangeInput(proposal_id=str(proposal.guid)),
        )
    assert not denied.ok
    assert denied.errors[0].code == "PERMISSION_DENIED"

    # Proposer can.
    with _ctx(org, actor=proposer):
        result = ServicesMutation().withdraw_secret_change(
            _info(user=proposer),
            input=WithdrawSecretChangeInput(proposal_id=str(proposal.guid)),
        )
    assert result.ok
    proposal.refresh_from_db()
    assert proposal.status == "withdrawn"


def test_expired_proposal_rejects_approval(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("proposer")
    approver = _make_user("approver")
    proposal = _make_proposal(app, proposer=proposer)
    proposal.expires_at = timezone.now() - timedelta(seconds=1)
    proposal.save(update_fields=["expires_at", "updated_at", "version"])

    with _ctx(org, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    proposal.refresh_from_db()
    assert proposal.status == "expired"


def test_approve_requires_eligibility_when_users_set(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=1)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    proposer = _make_user("proposer")
    ineligible = _make_user("ineligible")
    eligible = _make_user("eligible")
    app.secret_approver_users.set([eligible])
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=ineligible):
        denied = ServicesMutation().approve_secret_change(
            _info(user=ineligible),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )
    assert not denied.ok
    assert denied.errors[0].code == "PERMISSION_DENIED"

    with _ctx(org, actor=eligible):
        ok = ServicesMutation().approve_secret_change(
            _info(user=eligible),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )
    assert ok.ok
    proposal.refresh_from_db()
    assert proposal.status == "applied"


def test_approve_requires_permission():
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=1)
    proposer = _make_user("proposer")
    approver = _make_user("approver")
    proposal = _make_proposal(app, proposer=proposer)

    with _ctx(org, actor=approver):
        result = ServicesMutation().approve_secret_change(
            _info(user=approver),
            input=ApproveSecretChangeInput(proposal_id=str(proposal.guid)),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_list_secret_change_proposals_filters(permission_resolver):
    org, app, _, _ = _scaffold(requires_secret_approval=True, min_approvals=1)
    permission_resolver.grant(Permission.APP_READ)
    proposer = _make_user("proposer")
    _make_proposal(app, proposer=proposer, payload={"key": "K1", "value": "v"})
    p2 = _make_proposal(app, proposer=proposer, payload={"key": "K2", "value": "v"})
    p2.transition_to(SecretChangeProposal.Status.WITHDRAWN)

    with _ctx(org):
        all_p = ServicesQuery().astrolift_secret_change_proposals(
            _info(),
            app_slug=app.slug,
        )
        pending_only = ServicesQuery().astrolift_secret_change_proposals(
            _info(),
            app_slug=app.slug,
            status="pending",
        )
    assert len(all_p) == 2
    assert len(pending_only) == 1
    assert pending_only[0].status == "pending"
