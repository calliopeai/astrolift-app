"""Policy writes reject invalid JSON atomically through the real resolver."""

from uuid import uuid4

import pytest

from astrolift_identity.abac import CONDITION_KINDS
from astrolift_identity.models import Policy
from astrolift_identity.schema.mutations.policies import PolicyMutations
from astrolift_identity.schema.mutations.types import CreatePolicyInput, UpdatePolicyInput
from astrolift_identity.tests import test_operation_context_2164 as operation_support
from astrolift_identity.tests.test_access_enforcement_2157 import _no_opensearch  # noqa: F401
from core.permissions import Permission
from core.tests.utils.scope_world import as_tenant, bind_role

pytestmark = pytest.mark.django_db
operation_world = operation_support.operation_world
policy = operation_support.policy


@pytest.fixture
def admin(operation_world):
    w = operation_world
    bind_role(
        w.user,
        permissions=[Permission.ORG_UPDATE],
        kind="ORG",
        scope_id=w.world.org.pk,
        slug=f"admin-{uuid4().hex}",
    )
    return w


@pytest.mark.parametrize(
    "conditions",
    [
        {},
        [None],
        [{"kind": "unknown"}],
        [{"kind": "approval_required", "min_approvers": True}],
        [{"kind": "approval_required", "min_approvers": 0}],
        [{"kind": "freshness", "max_session_age_minutes": "10"}],
        [{"kind": "env_match", "env_in": []}],
        [{"kind": "env_match", "env_in": [1]}],
        [{"kind": "device_assertion", "required_factors": "sso"}],
        [{"kind": "ip_allowlist", "cidrs": ["300.0.0.0/8"]}],
        [{"kind": "time_window", "days": ["wat"], "hours": ["09:00-17:00"]}],
        [{"kind": "time_window", "days": ["mon"], "hours": ["09:00-09:00"]}],
        [{"kind": "time_window", "days": ["mon"], "hours": ["26:00-27:00"]}],
        [{"kind": "time_window", "days": ["mon"], "hours": ["09:00-17:00"], "tz": "Unknown/Timezone"}],
        [{"kind": "env_match", "env_in": ["staging"], "env_typo": []}],
    ],
)
def test_create_rejects_bad_condition_shapes_without_writing(admin, conditions):
    w = admin
    slug = f"invalid-{uuid4().hex}"
    with as_tenant(w.world, w.user):
        result = PolicyMutations().create_policy(
            w.info,
            CreatePolicyInput(name="Invalid", slug=slug, action_pattern="app.deploy", conditions=conditions),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field.startswith("conditions")
    assert not Policy.objects.filter(slug=slug).exists()


@pytest.mark.parametrize("kind", CONDITION_KINDS, ids=lambda kind: kind.kind)
def test_catalog_examples_are_valid_policy_writes(admin, kind):
    w = admin
    with as_tenant(w.world, w.user):
        result = PolicyMutations().create_policy(
            w.info,
            CreatePolicyInput(
                name="Valid",
                slug=f"valid-{uuid4().hex}",
                action_pattern="app.deploy",
                conditions=[kind.example],
            ),
        )
    assert result.ok, result.errors


@pytest.mark.parametrize(
    "change",
    [
        {"conditions": [{"kind": "approval_required", "min_approvers": -1}]},
        {"resource_pattern": {"environment_typo": ["production"]}},
        {"actor_pattern": {"user_in_groups": [12]}},
        {"effect": "UNKNOWN"},
    ],
)
def test_invalid_update_changes_no_fields_or_version(admin, change):
    w = admin
    row = Policy.objects.create(
        organization=w.world.org,
        slug=f"before-{uuid4().hex}",
        name="Before",
        action_pattern="app.deploy",
        conditions=[],
    )
    original_version = row.version
    with as_tenant(w.world, w.user):
        result = PolicyMutations().update_policy(
            w.info, UpdatePolicyInput(id=str(row.guid), name="After", **change)
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    row.refresh_from_db()
    assert row.name == "Before"
    assert row.version == original_version
