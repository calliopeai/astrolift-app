"""``[app] kind = "factory"``: registered beside mimwell, resolved the same way, never a brain."""

from __future__ import annotations

import pytest

from astrolift_manifest.app_kinds import APP_KINDS, BRAIN_KINDS, resolve_app_kind
from astrolift_manifest.discover import scan_app_manifests
from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.brains import visible_brains
from astrolift_registry.schema.queries import RegistryQuery
from core.edge_access import brain_fields
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

BODY = """
name = "solo-worker"
{app}
[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""


def manifest(app_table: str = "") -> str:
    return BODY.format(app=app_table)


def test_factory_is_registered_beside_mimwell():
    assert sorted(APP_KINDS) == ["factory", "mimwell"]
    assert (APP_KINDS["factory"].key, APP_KINDS["factory"].label) == ("factory", "Factory")


@pytest.mark.parametrize("value", ["factory", " Factory ", "FACTORY"])
def test_a_declared_factory_kind_resolves_like_any_registered_kind(value):
    declared = manifest(f'[app]\nkind = "{value}"\n')
    assert resolve_app_kind(value) == "factory"
    assert parse_raw(declared).app_kind == "factory"
    assert normalize(parse_raw(declared)).serialized["app_kind"] == "factory"
    [found] = scan_app_manifests({"astrolift.toml": declared})
    assert found.app_kind == "factory"


@pytest.fixture
def world():
    w = ScopeWorld("factorykind")
    w.medops_app.app_kind = "mimwell"
    w.medops_app.save(update_fields=["app_kind"])
    w.reader = make_user("factorykind")
    bind_role(
        w.reader, permissions=[Permission.APP_READ], kind="ORG", scope_id=w.org.pk, slug="reader-factorykind"
    )
    return w


def _as_reader(world):
    return tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.reader.pk))


@pytest.mark.django_db
def test_the_app_kinds_catalogue_lists_factory(world):
    with _as_reader(world):
        kinds = RegistryQuery().astrolift_app_kinds(make_info(world.reader))
    assert [(k.key, k.label) for k in kinds] == [("mimwell", "Mimwell"), ("factory", "Factory")]


@pytest.mark.django_db
def test_a_factory_app_takes_its_kind_from_its_manifest_and_is_never_a_brain(world):
    persist_manifest(world.platform_app, normalize(parse_raw(manifest('[app]\nkind = "factory"\n'))))
    world.platform_app.refresh_from_db()
    assert world.platform_app.app_kind == "factory"

    assert BRAIN_KINDS == frozenset({"mimwell"})
    with _as_reader(world):
        assert [app.slug for app in visible_brains()] == [world.medops_app.slug]
    assert brain_fields(world.platform_app) == {}
