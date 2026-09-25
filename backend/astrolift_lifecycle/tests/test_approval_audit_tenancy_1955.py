"""Deployment approval reads keep refused and foreign audit rows out (#1955).

``astroliftDeploymentApprovalHistory`` and ``AstroliftDeployment.approvedBy``
read ``AuditEvent`` rows by the deployment guid. ``@mutation_audit`` writes
one for every approve attempt, refused or not, and records the target from
the input before any check runs. Three ways an attempt that did not count
reached these reads:

* another org's refused attempt, filed under that org (round 1: the reads
  filter by org);
* an outsider naming this org in ``X-Astrolift-Organization``, which the
  middleware accepts without a membership check, so the refusal was filed
  under this org (the writers now attribute only to members);
* this org's own members refused, either by RBAC (a DENY row) or by the
  approver list (an ALLOW row, #1968): ``approvedBy`` now lists only ALLOW
  rows from eligible approvers.

Both routes take only a deployment guid: no slug, and the target match was
already exact, so these rows are the whole leak.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Member, Organization
from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    DeploymentByIdInput,
    LifecycleMutation,
    StartDeploymentInput,
)
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.schema.types import deployment_to_type
from astrolift_operations.audit_writer import write_audit_entry
from astrolift_operations.models import AuditEvent
from core.mutations import register_audit_writer
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def persistent_audit_writer():
    """Persist AuditEvent rows for the test, restoring whichever writer was
    installed before (another suite may have swapped it)."""
    from core import mutations as _mod

    previous = _mod._audit_writer
    register_audit_writer(write_audit_entry)
    yield
    register_audit_writer(previous)


def _member(user, org) -> Member:
    return Member.objects.create(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.id)


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Other", slug="other-1955")


@pytest.fixture
def outsider(other_org):
    user = get_user_model().objects.create(
        username="olga@other", email="olga@other", first_name="Olga", last_name="Outsider"
    )
    _member(user, other_org)
    return user


def _user_in(org, name: str):
    user = get_user_model().objects.create(username=f"{name}@test", email=f"{name}@test")
    _member(user, org)
    return user


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _tenant_for(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


@pytest.fixture
def pending(
    org,
    app,
    env_requires_approval,
    actor,
    other_actor,
    permission_resolver,
    persistent_audit_writer,
    no_temporal,
):
    """A deployment awaiting approval in ``org``, triggered by ``actor``, with
    ``other_actor`` its one listed approver; both are org members.
    ``app.approve_deploy`` is granted only where a test says so."""
    _member(actor, org)
    _member(other_actor, org)
    app.requires_approval = True
    app.save(update_fields=["requires_approval"])
    app.approver_users.set([other_actor])
    for permission in (Permission.APP_DEPLOY, Permission.APP_READ):
        permission_resolver.grant(permission)
    with _tenant_for(org, actor):
        start = LifecycleMutation().start_deployment(
            _info(actor),
            input=StartDeploymentInput(
                app_slug=app.slug, environment_name=env_requires_approval.name, image_tag="v1"
            ),
        )
    assert start.ok, start.errors
    return start.data.id


def _approve(org, user, deployment_id):
    with _tenant_for(org, user):
        return LifecycleMutation().approve_deployment(
            _info(user), input=DeploymentByIdInput(id=deployment_id)
        )


def _history(org, user, deployment_id) -> list:
    with _tenant_for(org, user):
        rows = LifecycleQuery().astrolift_deployment_approval_history(
            _info(user), deployment_id=str(deployment_id)
        )
    return [(h.action, h.actor_id) for h in rows]


def _approved_by(deployment_id) -> list:
    deployment = Deployment.objects.select_related("registered_app", "app_environment", "workload").get(
        guid=str(deployment_id)
    )
    return [(a.user_id, a.email) for a in deployment_to_type(deployment).approved_by]


def test_another_orgs_refused_approve_stays_out_of_the_approval_history(
    org, actor, other_org, outsider, pending, permission_resolver
):
    permission_resolver.grant(Permission.APP_APPROVE_DEPLOY)
    assert _approve(other_org, outsider, pending).ok is False
    # The refused attempt is on file in the other org, targeting this guid.
    assert AuditEvent.objects.filter(
        action="deployment.approve", target_id=str(pending), organization_id=other_org.id
    ).exists()

    assert _history(org, actor, pending) == [("deployment.start", str(actor.pk))]


def test_an_outsider_naming_the_org_stays_out_of_the_approval_history(org, actor, outsider, pending):
    """Review PoC: the outsider's session names ``org`` as its tenant. RBAC
    refuses the approve, and the row used to be filed under ``org``."""
    assert _approve(org, outsider, pending).ok is False
    assert AuditEvent.objects.get(action="deployment.approve", target_id=str(pending)).organization_id is None

    assert _history(org, actor, pending) == [("deployment.start", str(actor.pk))]


def test_an_outsider_naming_the_org_is_not_listed_as_an_approver(
    org, other_actor, outsider, pending, permission_resolver
):
    assert _approve(org, outsider, pending).ok is False
    permission_resolver.grant(Permission.APP_APPROVE_DEPLOY)
    assert _approve(org, other_actor, pending).ok

    assert _approved_by(pending) == [(str(other_actor.pk), other_actor.email)]


def test_a_member_refused_by_the_approver_list_is_not_listed(org, other_actor, pending, permission_resolver):
    """The refusal comes back as an envelope, which ``@mutation_audit``
    records as ALLOW (#1968), and the member's row is filed under the org:
    only eligibility keeps it out."""
    permission_resolver.grant(Permission.APP_APPROVE_DEPLOY)
    bystander = _user_in(org, "bystander")
    assert _approve(org, bystander, pending).ok is False
    assert _approve(org, other_actor, pending).ok

    assert _approved_by(pending) == [(str(other_actor.pk), other_actor.email)]


def test_a_listed_approver_refused_by_rbac_is_not_listed(org, app, other_actor, pending, permission_resolver):
    """A DENY row from someone on the approver list: only the decision keeps
    it out."""
    unbound = _user_in(org, "unbound")
    app.approver_users.add(unbound)
    assert _approve(org, unbound, pending).ok is False
    assert AuditEvent.objects.get(action="deployment.approve", actor_id=str(unbound.pk)).decision == "DENY"
    permission_resolver.grant(Permission.APP_APPROVE_DEPLOY)
    assert _approve(org, other_actor, pending).ok

    assert _approved_by(pending) == [(str(other_actor.pk), other_actor.email)]
