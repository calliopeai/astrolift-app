"""Deployment approval reads keep other orgs' audit rows out (#1955).

``astroliftDeploymentApprovalHistory`` and ``AstroliftDeployment.approvedBy``
read ``AuditEvent`` rows by the deployment guid. ``@mutation_audit`` records
that guid as the target from the input, before the resolver's org check, so
another org's refused ``approveDeployment`` on the guid leaves a row that
targets this deployment. Without the org filter it showed up in this org's
approval history, and its actor was listed as an approver by name and email.

Both routes take only a deployment guid: no slug, and the target match was
already exact, so the other org's row is the whole leak.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
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


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Other", slug="other-1955")


@pytest.fixture
def outsider():
    return get_user_model().objects.create(
        username="olga@other", email="olga@other", first_name="Olga", last_name="Outsider"
    )


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _tenant_for(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


@pytest.fixture
def pending(
    org, app, env_requires_approval, actor, permission_resolver, persistent_audit_writer, no_temporal
):
    """A deployment awaiting approval in ``org``."""
    for permission in (Permission.APP_DEPLOY, Permission.APP_APPROVE_DEPLOY, Permission.APP_READ):
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


def _refused_approve_from(other_org, outsider, deployment_id) -> None:
    with _tenant_for(other_org, outsider):
        result = LifecycleMutation().approve_deployment(
            _info(outsider), input=DeploymentByIdInput(id=deployment_id)
        )
    assert result.ok is False
    # The refused attempt is on file in the other org, targeting this guid.
    assert AuditEvent.objects.filter(
        action="deployment.approve", target_id=str(deployment_id), organization_id=other_org.id
    ).exists()


def test_other_orgs_refused_approve_stays_out_of_the_approval_history(
    org, actor, other_org, outsider, pending
):
    _refused_approve_from(other_org, outsider, pending)

    with _tenant_for(org, actor):
        history = LifecycleQuery().astrolift_deployment_approval_history(
            _info(actor), deployment_id=str(pending)
        )

    assert [h.action for h in history] == ["deployment.start"]
    assert str(outsider.pk) not in {h.actor_id for h in history}


def test_other_orgs_refused_approve_is_not_listed_as_an_approver(
    org, other_actor, other_org, outsider, pending
):
    _refused_approve_from(other_org, outsider, pending)
    with _tenant_for(org, other_actor):
        approved = LifecycleMutation().approve_deployment(
            _info(other_actor), input=DeploymentByIdInput(id=pending)
        )
    assert approved.ok, approved.errors

    deployment = Deployment.objects.select_related("registered_app", "app_environment", "workload").get(
        guid=str(pending)
    )
    rendered = deployment_to_type(deployment)

    assert [(a.user_id, a.email) for a in rendered.approved_by] == [(str(other_actor.pk), other_actor.email)]
