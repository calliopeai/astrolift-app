"""A project's kind comes from what its live apps declare, never from names (#2556)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_identity.schema.types import project_to_type
from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import RegisteredApp
from astrolift_registry.project_kinds import BRAIN, FACTORY, GENERIC, project_kind
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()

MANIFEST = """
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


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-2556")


@pytest.fixture
def team(org):
    return Team.objects.create(organization=org, name="Eng", slug="eng-2556")


def _project(org, team, slug):
    return Project.objects.create(organization=org, team=team, name=slug.title(), slug=slug)


def _app(project, slug, app_kind=""):
    return RegisteredApp.objects.create(
        organization=project.organization,
        team=project.team,
        project=project,
        name=slug,
        slug=slug,
        k8s_namespace=f"ns-{slug}",
        app_kind=app_kind,
    )


def test_an_undeclared_project_is_generic(org, team):
    empty = _project(org, team, "empty-2556")
    plain = _project(org, team, "plain-2556")
    _app(plain, "api-2556")
    _app(plain, "worker-2556")
    assert project_kind(str(empty.guid)) == GENERIC
    assert project_kind(str(plain.guid)) == GENERIC


def test_one_declared_factory_app_makes_its_project_a_factory(org, team):
    project = _project(org, team, "factory-2556")
    _app(project, "toolchain-engineer-2556")
    _app(project, "toolchain-qa-2556")
    _app(project, "solo-worker-2556", "factory")
    assert project_kind(str(project.guid)) == FACTORY


def test_a_name_never_makes_a_kind(org, team):
    project = _project(org, team, "calliope-factory")
    _app(project, "factory")
    _app(project, "conflict-semantic-layer")
    _app(project, "calliope-brain")
    assert project_kind(str(project.guid)) == GENERIC


def test_a_brain_project_holds_nothing_but_brains(org, team):
    brains = _project(org, team, "brains-2556")
    _app(brains, "layer-a-2556", "mimwell")
    _app(brains, "layer-b-2556", "mimwell")
    mixed = _project(org, team, "mixed-2556")
    _app(mixed, "layer-c-2556", "mimwell")
    _app(mixed, "web-2556")
    assert project_kind(str(brains.guid)) == BRAIN
    assert project_kind(str(mixed.guid)) == GENERIC


def test_a_factory_declaration_outranks_a_brain_in_the_same_project(org, team):
    project = _project(org, team, "both-2556")
    _app(project, "layer-d-2556", "mimwell")
    _app(project, "controller-2556", "factory")
    assert project_kind(str(project.guid)) == FACTORY


def test_only_live_apps_count(org, team):
    project = _project(org, team, "retired-2556")
    _app(project, "toolchain-2556")
    controller = _app(project, "controller-retired-2556", "factory")
    assert project_kind(str(project.guid)) == FACTORY
    controller.deleted_at = timezone.now()
    controller.save(update_fields=["deleted_at"])
    assert project_kind(str(project.guid)) == GENERIC

    brains = _project(org, team, "brain-and-retired-2556")
    _app(brains, "layer-e-2556", "mimwell")
    retired = _app(brains, "old-web-2556")
    retired.deleted_at = timezone.now()
    retired.save(update_fields=["deleted_at"])
    assert project_kind(str(brains.guid)) == BRAIN


def test_the_manifest_declares_it_and_takes_it_back(org, team):
    project = _project(org, team, "solo-2556")
    app = _app(project, "solo-2556")
    assert project_kind(str(project.guid)) == GENERIC

    persist_manifest(app, normalize(parse_raw(MANIFEST.format(app='[app]\nkind = "factory"\n'))))
    app.refresh_from_db()
    assert app.app_kind == "factory"
    assert project_kind(str(project.guid)) == FACTORY

    persist_manifest(app, normalize(parse_raw(MANIFEST.format(app=""))))
    app.refresh_from_db()
    assert app.app_kind == ""
    assert project_kind(str(project.guid)) == GENERIC


class _Request:
    def __init__(self, user):
        self.user = user
        self.session = {}
        self.headers = {}


WITH_KIND = "{ astroliftProjects { slug kind } astroliftProjectsPage { items { slug kind } } }"


def _projects(org, viewer, document=WITH_KIND):
    from config.schema import schema
    from core.schema.context import StrawberryContext

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=viewer.id)):
        result = schema.execute_sync(document, context_value=StrawberryContext(_Request(viewer)))
    assert result.errors is None, result.errors
    return result.data


def test_the_project_lists_carry_the_kind(permission_resolver, org, team):
    permission_resolver.grant(Permission.PROJECT_READ)
    viewer = User.objects.create_user(username="kinds-2556", email="kinds-2556@acme.test", password="pw")
    factory = _project(org, team, "list-factory-2556")
    _app(factory, "list-controller-2556", "factory")
    brain = _project(org, team, "list-brain-2556")
    _app(brain, "list-layer-2556", "mimwell")
    _project(org, team, "list-plain-2556")

    data = _projects(org, viewer)
    expected = {"list-factory-2556": FACTORY, "list-brain-2556": BRAIN, "list-plain-2556": GENERIC}
    assert {p["slug"]: p["kind"] for p in data["astroliftProjects"]} == expected
    assert {p["slug"]: p["kind"] for p in data["astroliftProjectsPage"]["items"]} == expected


def test_the_kind_adds_no_query_to_the_project_lists(permission_resolver, org, team):
    permission_resolver.grant(Permission.PROJECT_READ)
    viewer = User.objects.create_user(username="count-2556", email="count-2556@acme.test", password="pw")
    for n in range(5):
        project = _project(org, team, f"count-{n}-2556")
        _app(project, f"count-app-{n}-2556", "factory" if n % 2 else "mimwell")
    without_kind = WITH_KIND.replace(" kind", "")
    _projects(org, viewer, without_kind)

    with CaptureQueriesContext(connection) as plain:
        _projects(org, viewer, without_kind)
    with CaptureQueriesContext(connection) as kinded:
        data = _projects(org, viewer)

    assert len(data["astroliftProjects"]) == 5
    assert len(kinded.captured_queries) == len(plain.captured_queries)


def test_a_project_built_without_the_list_annotation_reads_its_own_kind(org, team):
    project = _project(org, team, "single-2556")
    _app(project, "single-controller-2556", "factory")
    plain = Project.objects.select_related("organization", "team").get(pk=project.pk)
    assert project_to_type(plain).kind() == FACTORY
