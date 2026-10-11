"""``[app] kind``: what an app is for, separate from workload kinds (#2332)."""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_manifest.discover import scan_app_manifests
from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import RegisteredApp

BODY = """
name = "mimwell"
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


@pytest.mark.parametrize(
    "app_table,expected",
    [
        ("", ""),
        ('[app]\nslug = "mimwell"\n', ""),
        ('[app]\nkind = "mimwell"\n', "mimwell"),
        ('[app]\nkind = " Mimwell "\n', "mimwell"),
        ('[app]\nkind = "web"\n', ""),
        ('[app]\nkind = "worker"\n', ""),
    ],
)
def test_declared_omitted_and_legacy_kinds(app_table, expected):
    raw = parse_raw(manifest(app_table))
    assert raw.app_kind == expected
    assert [w.kind for w in raw.workloads] == ["deployment"]


@pytest.mark.parametrize("value", ['"brainz"', "3", '["mimwell"]'])
def test_unknown_or_malformed_kind_is_a_located_error(value):
    with pytest.raises(ManifestError) as exc:
        parse_raw(manifest(f"[app]\nkind = {value}\n"))
    assert exc.value.path == "app.kind"
    if value == '"brainz"':
        assert "supported kinds: mimwell" in str(exc.value)


def test_a_generic_manifest_keeps_its_normalized_shape_and_hash():
    generic = normalize(parse_raw(manifest()))
    legacy = normalize(parse_raw(manifest('[app]\nkind = "web"\n')))
    kinded = normalize(parse_raw(manifest('[app]\nkind = "mimwell"\n')))
    assert "app_kind" not in generic.serialized
    assert legacy.serialized == generic.serialized
    assert kinded.serialized["app_kind"] == "mimwell"


def test_discovery_previews_the_kind():
    [found] = scan_app_manifests({"astrolift.toml": manifest('[app]\nkind = "mimwell"\n')})
    assert found.app_kind == "mimwell"


@pytest.mark.django_db
def test_persist_sets_and_clears_the_app_kind():
    org = Organization.objects.create(name="Acme", slug="acme-kind-2332")
    team = Team.objects.create(organization=org, name="T", slug="t-kind-2332")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-kind-2332")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Mimwell",
        slug="mimwell-2332",
        k8s_namespace="ns-2332",
    )

    persist_manifest(app, normalize(parse_raw(manifest('[app]\nkind = "mimwell"\n'))))
    app.refresh_from_db()
    assert app.app_kind == "mimwell"

    persist_manifest(app, normalize(parse_raw(manifest())))
    app.refresh_from_db()
    assert app.app_kind == ""
