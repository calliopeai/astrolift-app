"""start_workflow GFK-target org scoping (#1183).

The legacy ``start_workflow`` mutation (spec 40 §8, dormant) resolves an
arbitrary model + pk through a GenericForeignKey and starts a workflow against
it. ``@require_permission`` + ``@tenant_scoped`` above the resolver only assert
a capability and that *a* tenant context exists — neither FILTERS — so before
#1183 a ``WORKFLOW_TRIGGER`` holder in org A could start a workflow against org
B's object simply by passing its pk. These tests pin the fix: the target is
scoped to the caller's org, and a target model with no org linkage fails closed
(refused even when the row exists).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from graphql import GraphQLError

from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx
from workflows.models import WorkflowDefinition, WorkflowInstance
from workflows.schema.mutations import Mutation

pytestmark = pytest.mark.django_db

# A state machine with a single initial state so WorkflowInstance.start can
# construct an instance on the success path.
_STATES = [{"name": "draft", "label": "Draft", "is_initial": True, "is_final": False}]


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Org A", slug="sw-org-a")


@pytest.fixture
def other_org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Org B", slug="sw-org-b")


@pytest.fixture
def member(django_user_model):
    return django_user_model.objects.create_user(username="sw_member", email="m@t.com", password="pw")


def _trigger_definition(organization):
    """An enabled definition (with an initial state) for the caller to trigger."""
    return WorkflowDefinition.objects.create(
        name="Trigger WF",
        slug="sw-trigger",
        organization=organization,
        model_label="workflows.WorkflowDefinition",
        states=_STATES,
        transitions=[],
        is_enabled=True,
    )


def _target(slug, organization):
    """An org-scoped GFK target. WorkflowDefinition is a convenient in-domain
    model that carries an ``organization`` FK — the object-under-workflow only
    needs to be an org-owned row for these scoping assertions."""
    return WorkflowDefinition.objects.create(
        name=f"Target {slug}",
        slug=slug,
        organization=organization,
        model_label="",
        states=[],
        transitions=[],
        is_enabled=True,
    )


def test_start_workflow_same_org_object_succeeds(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    wf = _trigger_definition(org)
    target = _target("sw-mine", org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.start_workflow(
            _info(member),
            workflow_slug=wf.slug,
            model_label="workflows.WorkflowDefinition",
            object_id=target.pk,
        )
    assert res.ok is True
    assert res.instance_id
    inst = WorkflowInstance.objects.get(pk=int(res.instance_id))
    assert inst.object_id == target.pk


def test_start_workflow_cross_org_object_refused(member, org, other_org, permission_resolver):
    """The leak: org A starting a workflow against org B's object by pk. The
    foreign object must read as not-found (no cross-tenant existence oracle) and
    NO instance may be created. This would return ok=True on the pre-#1183
    code — the bare ``model.objects.get(pk=object_id)``."""
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    wf = _trigger_definition(org)
    foreign = _target("sw-theirs", other_org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(GraphQLError, match="not found"):
            m.start_workflow(
                _info(member),
                workflow_slug=wf.slug,
                model_label="workflows.WorkflowDefinition",
                object_id=foreign.pk,
            )
    assert not WorkflowInstance.objects.exists()


def test_start_workflow_model_without_org_linkage_fails_closed(member, org, permission_resolver):
    """A target model that cannot reach an organization (ContentType) is refused
    even though the row exists — we cannot prove it is the caller's, so we fail
    closed rather than run against a possibly-foreign object."""
    from django.contrib.contenttypes.models import ContentType

    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    wf = _trigger_definition(org)
    ct = ContentType.objects.first()
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(GraphQLError, match="not found"):
            m.start_workflow(
                _info(member),
                workflow_slug=wf.slug,
                model_label="contenttypes.ContentType",
                object_id=ct.pk,
            )
    assert not WorkflowInstance.objects.exists()
