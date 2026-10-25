# ruff: noqa: F811  (pytest fixtures imported from test_brain_management_2424)
"""Semantic layer follow-ups.

#2441: a brain's container verifies read tokens itself, so its routes keep the
token header, its workloads get the install's verification env, a new brain
builds the template-owned portal container and gets more memory than a static
site. #2444: the server pieces the web UI worked around (list paging, a
cached summary, the edition's cells and app_slug support, the port, share
audit rows, archived layers, every environment).
"""

from __future__ import annotations

import json
import tomllib
from types import SimpleNamespace

import pytest
from django.core.cache import cache

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry import brain_manage, brain_substrate, brain_template, brain_tokens
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.brain_types import BrainShareAccess, BrainSort
from astrolift_registry.schema.mutations import RegistryMutation
from astrolift_registry.schema.mutations.brains import ShareBrainInput, UnshareBrainInput
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.tests.test_brain_management_2424 import (  # noqa: F401 (fixtures)
    _as,
    _brain,
    _env,
    audit,
    host,
    other,
    started,
    world,
)
from astrolift_registry.tests.test_brain_scaffold_2424 import edition_zip
from astrolift_scm.providers import repo_writes
from core.app_deploy import envoy_edge_routes, render_resources_for_deployment
from providers.k8s_native.edge_gateway import BRAIN_TOKEN_HEADER, ID_TOKEN_HEADER, render_app_routes

pytestmark = pytest.mark.django_db

ISSUER = "https://astrolift.example.net"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# #2441
# ---------------------------------------------------------------------------


def _removed(out, name=None):
    [route] = [r for r in out if r["kind"] == "HTTPRoute" and (name is None or r["metadata"]["name"] == name)]
    return route["spec"]["rules"][0]["filters"][0]["requestHeaderModifier"]["remove"]


BRAIN = {
    "brain_audience": "01a0-kb",
    "brain_issuer": "https://astrolift.acme.test",
    "brain_jwks_uri": "https://astrolift.acme.test/api/brains/v1/jwks.json",
}


def test_only_a_brains_mcp_route_keeps_the_token():
    """The token reaches the container on ``/mcp`` alone (#2526); every app route strips it."""
    common = {
        "app_slug": "kb",
        "namespace": "acme-kb",
        "workloads": {"brain": (["kb.acme.test"], 8080)},
        "gated": True,
        "paused": False,
    }
    out = render_app_routes(**common, brain=BRAIN)
    assert _removed(out, "acme-kb-brain-mcp") == [ID_TOKEN_HEADER]
    assert _removed(out, "acme-kb-brain") == [ID_TOKEN_HEADER, BRAIN_TOKEN_HEADER]
    assert _removed(render_app_routes(**common)) == [ID_TOKEN_HEADER, BRAIN_TOKEN_HEADER]

    cluster = SimpleNamespace(
        oidc_auth_config=None,
        ingress_class="envoy",
        slug="c",
        provider_plugin=SimpleNamespace(slug="aws"),
        ingress_mode="shared_ingress",
    )

    def app(kind):
        return SimpleNamespace(
            slug="kb",
            guid="01a0-kb",
            app_kind=kind,
            manifest_normalized={},
            organization_id=1,
            organization=SimpleNamespace(slug="acme"),
        )

    # No gate on the cluster: nothing to pass, so no MCP route either.
    for kind in ("mimwell", ""):
        out = envoy_edge_routes(
            app(kind), namespace="ns", workloads={"web": (["a.z.example"], 80)}, cluster=cluster, paused=False
        )
        assert _removed(out) == [ID_TOKEN_HEADER, BRAIN_TOKEN_HEADER]


BRAIN_MANIFEST = """
name = "acme-brain"

[app]
kind = "mimwell"

[[workloads]]
name = "brain"
kind = "deployment"
is_public = true

[[workloads.containers]]
name = "brain"
is_primary = true
image_ref = "ghcr.io/acme/brain:1"
port = 8080

[workloads.containers.env]
BRAIN_MCP_AUDIENCE = "chosen-by-the-repo"
KEEP = "me"
"""


def _rendered_env(world, app, settings):
    settings.APP_BASE_URL = ISSUER
    app.manifest_raw = BRAIN_MANIFEST.replace("acme-brain", app.slug)
    app.save(update_fields=["manifest_raw"])
    env = AppEnvironment.objects.get(registered_app=app, name="production")
    deployment = Deployment.objects.create(
        registered_app=app, app_environment=env, trigger_kind="manual", status="pending", image_tag="1"
    )
    rendered = render_resources_for_deployment(deployment)
    [web] = [r for r in rendered if r["kind"] == "Deployment"]
    return {e["name"]: e.get("value") for e in web["spec"]["template"]["spec"]["containers"][0]["env"]}


def test_a_brains_workloads_verify_tokens_with_the_installs_values(world, settings):
    app = _brain(world)

    env = _rendered_env(world, app, settings)

    assert env["BRAIN_MCP_JWKS_URL"] == f"{ISSUER}{brain_tokens.JWKS_PATH}"
    assert env["BRAIN_MCP_ISSUER"] == ISSUER
    # From the install, never the brain repo.
    assert env["BRAIN_MCP_AUDIENCE"] == str(app.guid)
    assert env["KEEP"] == "me"


def test_any_other_app_gets_no_verification_env(world, settings):
    app = _brain(world)
    RegisteredApp.objects.filter(pk=app.pk).update(app_kind="")
    app.refresh_from_db()

    env = _rendered_env(world, app, settings)

    assert "BRAIN_MCP_JWKS_URL" not in env and env["BRAIN_MCP_AUDIENCE"] == "chosen-by-the-repo"


def test_a_new_brain_builds_the_template_owned_container_with_more_memory():
    rendered = tomllib.loads(
        brain_substrate.render_manifest(brain_substrate.Selection(), slug="kb", display_name="KB")
    )
    [workload] = rendered["workloads"]
    [container] = workload["containers"]
    assert (container["dockerfile_path"], container["build_context"]) == (
        "runtime/portal-container/Dockerfile",
        ".",
    )
    assert brain_template.DOCKERFILE == "runtime/portal-container/Dockerfile"
    assert (workload["memory_request"], workload["memory_limit"]) == ("128Mi", "512Mi")


@pytest.mark.parametrize(
    "memory_request,memory_limit,expected",
    [
        ("256Mi", "64Mi", ("256Mi", "512Mi")),
        ("1Gi", "2Gi", ("1Gi", "2Gi")),
        ("64Mi", "256Mi", ("128Mi", "512Mi")),
    ],
)
def test_graduation_keeps_a_bigger_brains_memory_and_its_own_dockerfile(
    memory_request, memory_limit, expected
):
    own = f"""
name = "kb"

[[workloads]]
name = "brain"
kind = "deployment"
memory_request = "{memory_request}"
memory_limit = "{memory_limit}"

[[workloads.containers]]
name = "brain"
dockerfile_path = "extensions/portal-container/Dockerfile"
build_context = "."
"""
    rendered = brain_substrate.render_manifest(brain_substrate.Selection(), slug="kb", display_name="KB")

    merged = tomllib.loads(brain_substrate.merge_manifest(own, rendered))

    [workload] = merged["workloads"]
    # More than the default stays; less is raised to it.
    assert (workload["memory_request"], workload["memory_limit"]) == expected
    assert workload["containers"][0]["dockerfile_path"] == "extensions/portal-container/Dockerfile"
    assert workload["containers"][0]["build_context"] == "."


# ---------------------------------------------------------------------------
# #2444
# ---------------------------------------------------------------------------


def _layer(world, slug, name="KB"):
    """A brain of the 2424 rig with its own repository and name."""
    app = _brain(world, slug=slug)
    RegisteredApp.objects.filter(pk=app.pk).update(name=name, source_repo=f"acme/{slug}")
    app.refresh_from_db()
    return app


def _page(world, **kwargs):
    with _as(world) as info:
        return RegistryQuery().astrolift_brains_page(info, **kwargs)


def test_the_list_pages_on_the_server_with_a_total_team_filter_and_sort(world):
    for slug, name in (("b-one", "Bravo"), ("a-two", "Alpha"), ("c-three", "Charlie")):
        _layer(world, slug, name)
    platform = _layer(world, "p-four", "Delta")
    RegisteredApp.objects.filter(pk=platform.pk).update(team=world.platform)

    first = _page(world, sort=BrainSort.NAME_ASC, limit=2)
    assert [b.name for b in first.items] == ["Alpha", "Bravo"]
    assert first.total_count == 4 and first.next_cursor
    second = _page(world, sort=BrainSort.NAME_ASC, limit=2, after=first.next_cursor)
    assert [b.name for b in second.items] == ["Charlie", "Delta"] and second.next_cursor is None
    assert [b.name for b in _page(world, sort=BrainSort.NAME_DESC, limit=1).items] == ["Delta"]
    assert [b.slug for b in _page(world, sort=BrainSort.NEWEST, limit=1).items] == ["p-four"]
    team = _page(world, team=world.platform.slug)
    assert [b.slug for b in team.items] == ["p-four"] and team.total_count == 1
    assert _page(world, search="charl").total_count == 1


def test_archived_layers_list_only_when_asked(world, other):
    from django.utils import timezone

    live = _layer(world, "live", "Live")
    gone = _layer(world, "gone", "Gone")
    RegisteredApp.objects.filter(pk=gone.pk).update(archived_at=timezone.now())
    _brain(other, slug="theirs")

    assert [b.slug for b in _page(world).items] == [live.slug]
    listed = _page(world, include_archived=True, sort=BrainSort.NAME_ASC)
    assert [(b.slug, b.archived) for b in listed.items] == [("gone", True), ("live", False)]


def test_the_summary_and_environments_come_without_reading_the_host(world, monkeypatch):
    app = _brain(world)
    env = AppEnvironment.objects.get(registered_app=app)
    AppEnvironment.objects.create(registered_app=app, tenant_cluster=world.cluster, name="staging")
    Deployment.objects.create(
        registered_app=app, app_environment=env, trigger_kind="push", status="running", commit_sha="abc"
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflows._pick_source_connection", lambda a: world.connection
    )
    monkeypatch.setattr(repo_writes, "branch_head_detail", lambda c, r, b: ("new", "2026-10-16T00:00:00Z"))
    monkeypatch.setattr(
        repo_writes,
        "latest_workflow_run",
        lambda c, r, f: {"status": "completed", "conclusion": "success", "url": "u", "created_at": "t"},
    )
    monkeypatch.setattr(
        "astrolift_scm.providers.fetch_file",
        lambda connection, *, repo_full_name, path, ref: json.dumps({"generated": "2026-10-15"}),
    )

    [before] = _page(world).items
    assert before.summary.environment_name == "production"
    assert before.summary.deployment.status == "running"
    assert before.summary.checked_at is None and before.summary.compiled_at is None
    assert [(e.name, e.served, e.deployment_status) for e in before.all_environments] == [
        ("production", True, "running"),
        ("staging", False, None),
    ]
    assert [e.name for e in before.environments] == ["production"]  # what clients connect to

    with _as(world) as info:
        RegistryQuery().astrolift_brain_status(info, slug=app.slug)

    def no_host(*args, **kwargs):
        raise AssertionError("a list never reads the source host")

    monkeypatch.setattr(brain_manage, "status", no_host)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", no_host)
    [after] = _page(world).items
    assert (after.summary.compiled_at, after.summary.behind) == ("2026-10-15", True)
    assert after.summary.last_ingest.conclusion == "success" and after.summary.checked_at


def test_the_edition_says_what_a_new_layer_can_be(world, monkeypatch):
    downloads = []

    def download(ref):
        downloads.append(ref)
        return edition_zip()

    monkeypatch.setattr(brain_template, "download", download)
    with _as(world) as info:
        edition = RegistryQuery().astrolift_brain_edition(info)
        again = RegistryQuery().astrolift_brain_edition(info)
        bad = RegistryQuery().astrolift_brain_edition(info, template_ref="../x")

    assert edition.ref == brain_template.default_ref() and edition.error is None
    assert edition.supports_app_slug is True and edition.ships_portal_container is True
    assert downloads == [brain_template.default_ref()]  # kept per ref
    assert again.supports_app_slug is True
    assert bad.supports_app_slug is None and "templateRef" in bad.error


def test_the_cells_are_mimwells_matrix(world):
    with _as(world) as info:
        found = RegistryQuery().astrolift_brain_cells(info)

    assert len(found) == 192
    cells = {(c.authority, c.blobs, c.vectors, c.graph): (c.status, c.reason) for c in found}
    assert cells[("files", "fs", "local", "edges-table")] == ("supported", "")
    assert cells[("d1", "fs", "local", "edges-table")] == (
        "unsupported",
        "d1 is a projection target, never an authority",
    )


def test_the_substrate_says_its_port(world):
    app = _brain(world)
    brain_manage.record_substrate(app, brain_substrate.Selection(port=9000))
    with _as(world) as info:
        assert RegistryQuery().astrolift_brain(info, slug=app.slug).substrate.port == 9000


def test_share_audit_rows_record_the_team_and_level_asked_for(world, audit):
    app = _brain(world)
    with _as(world) as info:
        RegistryMutation().share_brain(
            info,
            input=ShareBrainInput(
                slug=app.slug, team_slug=world.platform.slug, access=BrainShareAccess.VIEWER
            ),
        )
        RegistryMutation().unshare_brain(
            info, input=UnshareBrainInput(slug=app.slug, team_slug=world.platform.slug)
        )
    share, unshare = (row.extra for row in audit)
    assert (share["team"], share["access"]) == (world.platform.slug, "viewer")
    assert unshare["team"] == world.platform.slug and "access" not in unshare
