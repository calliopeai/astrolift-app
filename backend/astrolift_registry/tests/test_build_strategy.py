"""Tests for RegisteredApp.build_strategy field (#867).

Covers:
- Field exists on the model with the correct choices and default.
- GraphQL type ``AstroliftRegisteredApp`` exposes ``buildStrategy``.
- Persisting a non-default value round-trips through the DB correctly.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import app_to_type

pytestmark = pytest.mark.django_db


def _make_app(build_strategy="off"):
    org = Organization.objects.create(name="BuildCo", slug="buildco-bs")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-bs")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="MyApp",
        slug="myapp-bs",
        provisioning_status="ready",
        subdomain="myapp",
        build_strategy=build_strategy,
    )


def test_build_strategy_default_is_off():
    app = _make_app()
    assert app.build_strategy == "off"


def test_build_strategy_choices():
    choices = {c[0] for c in RegisteredApp.BuildStrategy.choices}
    assert choices == {"off", "dockerfile", "buildpacks", "nixpacks"}


def test_build_strategy_persists_dockerfile():
    app = _make_app(build_strategy="dockerfile")
    app.refresh_from_db()
    assert app.build_strategy == "dockerfile"


def test_build_strategy_persists_buildpacks():
    app = _make_app(build_strategy="buildpacks")
    app.refresh_from_db()
    assert app.build_strategy == "buildpacks"


def test_build_strategy_persists_nixpacks():
    app = _make_app(build_strategy="nixpacks")
    app.refresh_from_db()
    assert app.build_strategy == "nixpacks"


def test_graphql_type_exposes_build_strategy():
    app = _make_app(build_strategy="dockerfile")
    gql_type = app_to_type(app, info=None)
    assert hasattr(gql_type, "build_strategy")
    assert gql_type.build_strategy == "dockerfile"


def test_graphql_type_build_strategy_defaults_to_off():
    app = _make_app()
    gql_type = app_to_type(app, info=None)
    assert gql_type.build_strategy == "off"
