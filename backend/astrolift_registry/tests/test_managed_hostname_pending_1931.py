"""A managed domain still awaiting its TXT proof-of-control challenge must
not render as an app's hostname (#1931).

``_managed_hostnames_for_apps`` re-implements ``resolve_managed_domain``'s
logic against a pre-annotated queryset for the app list (#1043), so it needs
its own check that it excludes a pending row the same way.
"""

from __future__ import annotations

import uuid

import pytest

from astrolift_clusters.models import ManagedDomain
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import _annotate_managed_domain, _managed_hostnames_for_apps

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, g: None))


def _app(org, **overrides):
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{uuid.uuid4().hex[:6]}")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug=f"demo-{uuid.uuid4().hex[:6]}"
    )
    defaults = {
        "organization": org,
        "team": team,
        "project": project,
        "name": "Web",
        "slug": f"web-{uuid.uuid4().hex[:6]}",
        "source_kind": RegisteredApp.SourceKind.GITHUB,
        "registry_repo_uri": "",
        "push_role_ref": "",
    }
    defaults.update(overrides)
    return RegisteredApp.objects.create(**defaults)


def test_a_pending_platform_zone_is_not_used_as_a_hostname():
    org = Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")
    ManagedDomain.objects.create(
        zone="pending-platform.example",
        dns_driver="route53",
        default_for=ManagedDomain.DefaultFor.TENANT_APPS,
        verification_state=ManagedDomain.VerificationState.PENDING,
        verification_token="tok",
    )
    app = _app(org)

    qs = _annotate_managed_domain(RegisteredApp.objects.filter(pk=app.pk))
    hostnames = _managed_hostnames_for_apps(list(qs))

    assert hostnames.get(app.pk, "") == ""


def test_a_pending_org_default_is_not_used_as_a_hostname():
    org = Organization.objects.create(name="Acme2", slug=f"acme2-{uuid.uuid4().hex[:6]}")
    pending = ManagedDomain.objects.create(
        organization=org,
        zone="pending-own.example",
        dns_driver="route53",
        verification_state=ManagedDomain.VerificationState.PENDING,
        verification_token="tok",
    )
    org.default_managed_domain = pending
    org.save()
    app = _app(org)

    qs = _annotate_managed_domain(RegisteredApp.objects.filter(pk=app.pk))
    hostnames = _managed_hostnames_for_apps(list(qs))

    assert hostnames.get(app.pk, "") == ""


def test_a_verified_org_default_is_used_as_a_hostname():
    org = Organization.objects.create(name="Acme3", slug=f"acme3-{uuid.uuid4().hex[:6]}")
    verified = ManagedDomain.objects.create(
        organization=org,
        zone="verified-own.example",
        dns_driver="route53",
        verification_state=ManagedDomain.VerificationState.VERIFIED,
        verification_token="tok",
    )
    org.default_managed_domain = verified
    org.save()
    app = _app(org)

    qs = _annotate_managed_domain(RegisteredApp.objects.filter(pk=app.pk))
    hostnames = _managed_hostnames_for_apps(list(qs))

    assert hostnames.get(app.pk, "") == f"{app.slug}.verified-own.example"
