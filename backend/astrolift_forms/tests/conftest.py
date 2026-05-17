"""Per-app fixtures for the forms tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_forms.models import FormDefinition
from astrolift_identity.models import Organization
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx


@pytest.fixture
def info():
    """A minimal Strawberry ``Info`` stand-in.

    The forms resolvers only ever touch ``info.context.request``
    (for X-Forwarded-For / User-Agent capture on submit). Tests that
    don't care about ip / UA can pass ``request=None``.
    """

    def _make(request=None):
        return SimpleNamespace(context=SimpleNamespace(user=None, request=request))

    return _make


@pytest.fixture
def org():
    return Organization.objects.create(name="Forms Acme", slug="forms-acme")


@pytest.fixture
def other_org():
    """A second org so cross-tenant isolation can be asserted."""
    return Organization.objects.create(name="Forms Other", slug="forms-other")


@pytest.fixture
def with_tenant_org():
    """Context-manager helper: bind a TenantContext for the given org."""

    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


@pytest.fixture
def draft_form(org):
    return FormDefinition.objects.create(
        organization=org,
        name="Onboarding survey",
        slug="onboarding-survey",
        description="Welcome aboard.",
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "title": "Name"},
                "team_size": {"type": "number", "title": "Team size"},
            },
            "required": ["name"],
        },
        status=FormDefinition.Status.DRAFT,
    )


@pytest.fixture
def published_form(org):
    return FormDefinition.objects.create(
        organization=org,
        name="NPS",
        slug="nps",
        description="How likely are you to recommend us?",
        schema={
            "type": "object",
            "properties": {
                "score": {"type": "number", "title": "Score"},
                "comment": {"type": "string", "title": "Comment"},
            },
            "required": ["score"],
        },
        status=FormDefinition.Status.PUBLISHED,
        published_at="2026-05-01T00:00:00Z",
        schema_version=2,
    )
