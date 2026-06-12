"""Tests for RegisteredApp.build_mode field (#867).

Covers:
- Field exists on the model with the correct choices and default.
- GraphQL type ``AstroliftRegisteredApp`` exposes ``buildMode``.
- Persisting a non-default value round-trips through the DB correctly.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import app_to_type


pytestmark = pytest.mark.django_db


def _make_app(build_mode="off"):
    org = Organization.objects.create(name="BuildCo", slug="buildco-bm")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-bm")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="MyApp",
        slug="myapp-bm",
        provisioning_status="ready",
        subdomain="myapp",
        build_mode=build_mode,
    )


def test_build_mode_default_is_off():
    app = _make_app()
    assert app.build_mode == "off"


def test_build_mode_choices():
    choices = {c[0] for c in RegisteredApp.BuildMode.choices}
    assert choices == {"off", "dockerfile", "buildpacks", "nixpacks"}


def test_build_mode_persists_dockerfile():
    app = _make_app(build_mode="dockerfile")
    app.refresh_from_db()
    assert app.build_mode == "dockerfile"


def test_build_mode_persists_buildpacks():
    app = _make_app(build_mode="buildpacks")
    app.refresh_from_db()
    assert app.build_mode == "buildpacks"


def test_build_mode_persists_nixpacks():
    app = _make_app(build_mode="nixpacks")
    app.refresh_from_db()
    assert app.build_mode == "nixpacks"


def test_graphql_type_exposes_build_mode():
    app = _make_app(build_mode="dockerfile")
    gql_type = app_to_type(app)
    assert hasattr(gql_type, "build_mode")
    assert gql_type.build_mode == "dockerfile"


def test_graphql_type_build_mode_defaults_to_off():
    app = _make_app()
    gql_type = app_to_type(app)
    assert gql_type.build_mode == "off"
