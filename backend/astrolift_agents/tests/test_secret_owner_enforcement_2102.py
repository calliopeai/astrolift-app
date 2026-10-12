"""Owner enforcement for typed agent secret refs, behind its install switch (#2102)."""

import pytest
from constance.test import override_config

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.services.secret_owner_namespaces import agent_secret_prefix
from astrolift_agents.tests import secret_owner_migration_support as owner_support
from astrolift_dispatch.agent_secrets import (
    SecretRefNamespaceError,
    assert_org_scoped_secret_ref,
    unscoped_secret_refs,
)

pytestmark = pytest.mark.django_db

world = owner_support.world
no_index = owner_support.no_index


def _legacy(world):
    return f"agents/{world.org.guid}/credential"


def test_switch_off_keeps_the_compatible_org_check(world):
    assert_org_scoped_secret_ref(_legacy(world), organization=world.org, spec=world.own)
    assert_org_scoped_secret_ref(
        agent_secret_prefix(world.sibling) + "x", organization=world.org, spec=world.own
    )


@override_config(AGENT_SECRET_OWNER_ENFORCEMENT=True)
def test_switch_on_holds_a_ref_to_its_owner_on_write(world):
    assert_org_scoped_secret_ref(agent_secret_prefix(world.own) + "x", organization=world.org, spec=world.own)
    for foreign in (
        _legacy(world),
        agent_secret_prefix(world.sibling) + "x",
        agent_secret_prefix(world.shared) + "x",
    ):
        with pytest.raises(SecretRefNamespaceError, match="spec owner"):
            assert_org_scoped_secret_ref(foreign, organization=world.org, spec=world.own)
    # An unsaved spec is judged against the owner it is about to have.
    draft = AgentEnvironmentSpec(organization=world.org, team=world.medops, project=None)
    assert_org_scoped_secret_ref(
        agent_secret_prefix(world.team_spec) + "x", organization=world.org, spec=draft
    )


def test_dispatch_refuses_a_legacy_ref_only_once_enforced(world):
    world.own.secret_refs = [{"env_var": "TOKEN", "uri": _legacy(world)}]
    world.own.save()
    assert unscoped_secret_refs(world.own) == {}
    with override_config(AGENT_SECRET_OWNER_ENFORCEMENT=True):
        assert "spec owner" in unscoped_secret_refs(world.own)["TOKEN"]
        assert unscoped_secret_refs(world.sibling) == {}
