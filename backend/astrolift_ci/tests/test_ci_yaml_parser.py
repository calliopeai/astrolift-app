"""Tests for astrolift_ci.services.ci_yaml_parser (#870).

Covers:
- valid YAML parses correctly
- invalid YAML returns validation errors
- CiPipeline can be created with FK to org
"""

from __future__ import annotations

import textwrap

import pytest

from astrolift_ci.models import CiPipeline
from astrolift_ci.services.ci_yaml_parser import (
    BUILTIN_ACTIONS,
    CiYamlParseError,
    parse_ci_config,
    validate_ci_config,
)
from astrolift_identity.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Silence the User -> Profile -> OpenSearch indexing chain."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="CI Test Org", slug="ci-test-org")


# ---------------------------------------------------------------------------
# parse_ci_config — happy paths
# ---------------------------------------------------------------------------


def test_parse_minimal_valid_yaml():
    yaml_text = textwrap.dedent("""\
        ci:
          - name: test
            runs-on: ubuntu-latest
            steps:
              - name: Checkout
                uses: actions/checkout@v4
              - name: Run tests
                run: pytest
    """)
    config = parse_ci_config(yaml_text)
    assert "ci" in config
    assert len(config["ci"]) == 1
    job = config["ci"][0]
    assert job["name"] == "test"
    assert job["runs-on"] == "ubuntu-latest"
    assert len(job["steps"]) == 2


def test_parse_multi_job_yaml():
    yaml_text = textwrap.dedent("""\
        ci:
          - name: lint
            steps:
              - name: Lint
                run: ruff check .
          - name: test
            steps:
              - name: Test
                run: pytest
    """)
    config = parse_ci_config(yaml_text)
    assert len(config["ci"]) == 2
    assert config["ci"][0]["name"] == "lint"
    assert config["ci"][1]["name"] == "test"


def test_parse_empty_yaml_returns_empty_dict():
    config = parse_ci_config("")
    assert config == {}


def test_parse_uses_step_with_with_params():
    yaml_text = textwrap.dedent("""\
        ci:
          - name: build
            steps:
              - name: Setup Python
                uses: actions/setup-python@v5
                with:
                  python-version: "3.12"
    """)
    config = parse_ci_config(yaml_text)
    step = config["ci"][0]["steps"][0]
    assert step["uses"] == "actions/setup-python@v5"
    assert step["with"]["python-version"] == "3.12"


# ---------------------------------------------------------------------------
# parse_ci_config — error paths
# ---------------------------------------------------------------------------


def test_parse_raises_on_invalid_yaml():
    with pytest.raises(CiYamlParseError):
        parse_ci_config("ci: [unclosed bracket")


def test_parse_raises_when_top_level_not_mapping():
    with pytest.raises(CiYamlParseError):
        parse_ci_config("- item1\n- item2\n")


# ---------------------------------------------------------------------------
# validate_ci_config — valid configs
# ---------------------------------------------------------------------------


def test_validate_valid_config_returns_no_errors():
    config = {
        "ci": [
            {
                "name": "test",
                "steps": [
                    {"name": "Run", "run": "pytest"},
                ],
            }
        ]
    }
    assert validate_ci_config(config) == []


def test_validate_uses_step_is_valid():
    config = {
        "ci": [
            {
                "name": "build",
                "steps": [
                    {"name": "Checkout", "uses": "actions/checkout@v4"},
                ],
            }
        ]
    }
    assert validate_ci_config(config) == []


# ---------------------------------------------------------------------------
# validate_ci_config — structural errors
# ---------------------------------------------------------------------------


def test_validate_missing_ci_key():
    errors = validate_ci_config({})
    assert any("ci" in e for e in errors)


def test_validate_ci_not_a_list():
    errors = validate_ci_config({"ci": "not-a-list"})
    assert len(errors) == 1
    assert "list" in errors[0]


def test_validate_empty_jobs_list():
    errors = validate_ci_config({"ci": []})
    assert any("at least one job" in e for e in errors)


def test_validate_job_missing_name():
    config = {"ci": [{"steps": [{"run": "echo ok"}]}]}
    errors = validate_ci_config(config)
    assert any("name" in e for e in errors)


def test_validate_job_missing_steps():
    config = {"ci": [{"name": "build"}]}
    errors = validate_ci_config(config)
    assert any("steps" in e for e in errors)


def test_validate_empty_steps_list():
    config = {"ci": [{"name": "build", "steps": []}]}
    errors = validate_ci_config(config)
    assert any("at least one step" in e for e in errors)


def test_validate_step_missing_uses_and_run():
    config = {"ci": [{"name": "build", "steps": [{"name": "Orphan"}]}]}
    errors = validate_ci_config(config)
    assert any("uses" in e and "run" in e for e in errors)


def test_validate_step_with_both_uses_and_run():
    config = {
        "ci": [
            {
                "name": "build",
                "steps": [{"name": "Clash", "uses": "actions/checkout@v4", "run": "echo hi"}],
            }
        ]
    }
    errors = validate_ci_config(config)
    assert any("both" in e for e in errors)


def test_validate_non_dict_config():
    errors = validate_ci_config("not-a-dict")
    assert len(errors) == 1
    assert "mapping" in errors[0]


# ---------------------------------------------------------------------------
# BUILTIN_ACTIONS set
# ---------------------------------------------------------------------------


def test_builtin_actions_contains_checkout():
    assert "actions/checkout" in BUILTIN_ACTIONS


def test_builtin_actions_contains_all_required():
    required = {
        "actions/checkout",
        "actions/setup-python",
        "actions/setup-node",
        "actions/setup-go",
        "actions/cache",
        "astrolift/docker-build-push",
        "astrolift/deploy",
    }
    assert required <= BUILTIN_ACTIONS


# ---------------------------------------------------------------------------
# CiPipeline model — creation with FK to org
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_cipipeline_can_be_created(org):
    pipeline = CiPipeline.objects.create(
        organization=org,
        slug="test-pipeline",
        name="Test Pipeline",
        config_yaml="ci:\n  - name: test\n    steps:\n      - run: pytest\n",
    )
    assert pipeline.pk is not None
    assert pipeline.status == CiPipeline.status.field.default
    assert pipeline.organization == org
    assert pipeline.registered_app is None
    assert pipeline.last_synced_at is None


@pytest.mark.django_db(transaction=True)
def test_cipipeline_default_status_is_active(org):
    pipeline = CiPipeline.objects.create(
        organization=org,
        slug="active-pipeline",
        name="Active Pipeline",
    )
    assert pipeline.status == "active"


@pytest.mark.django_db(transaction=True)
def test_cipipeline_slug_unique_per_org(org):
    from django.db import IntegrityError

    CiPipeline.objects.create(organization=org, slug="dupe", name="Original")
    with pytest.raises(IntegrityError):
        CiPipeline.objects.create(organization=org, slug="dupe", name="Duplicate")


@pytest.mark.django_db(transaction=True)
def test_cipipeline_slug_reusable_after_soft_delete(org):
    pipeline = CiPipeline.objects.create(organization=org, slug="reuse-me", name="First")
    pipeline.soft_delete()
    # After soft-delete the unique partial index no longer blocks a new row.
    new_pipeline = CiPipeline.objects.create(organization=org, slug="reuse-me", name="Second")
    assert new_pipeline.pk != pipeline.pk


@pytest.mark.django_db(transaction=True)
def test_cipipeline_str(org):
    pipeline = CiPipeline.objects.create(organization=org, slug="str-test", name="Str Test")
    assert "str-test" in str(pipeline)
    assert "active" in str(pipeline)
