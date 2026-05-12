"""Tests for universal cloud-resource tagging schema (#30 backbone)."""

from __future__ import annotations

import pytest

from core.cloud_tags import (
    MIN_REQUIRED_KEYS,
    PLATFORM_NAMESPACE,
    TAG_AGENT_ID,
    TAG_AGENT_RUN_ID,
    TAG_APP,
    TAG_BINDING,
    TAG_ENV,
    TAG_INSTALL,
    TAG_ORG,
    CloudTagSet,
    TagEnforcementError,
    assert_required_tags,
    for_agent_run,
    to_aws,
    to_azure,
    to_gcp,
)


def _base() -> CloudTagSet:
    return CloudTagSet(
        install_slug="acme-prod",
        org_slug="acme",
        app_slug="api",
        env_slug="prod",
    )


# ---- canonical schema ----------------------------------------------


def test_required_fields_must_be_non_empty():
    """Every resource the platform provisions MUST be cost-
    attributable. Missing org_slug = bill goes to nobody."""
    for empty_field in ("install_slug", "org_slug", "app_slug", "env_slug"):
        kwargs = {
            "install_slug": "x",
            "org_slug": "x",
            "app_slug": "x",
            "env_slug": "x",
        }
        kwargs[empty_field] = ""
        with pytest.raises(ValueError, match=empty_field):
            CloudTagSet(**kwargs)


def test_extra_keys_cant_collide_with_platform_namespace():
    """Operator-defined extras must not silently overwrite
    platform tags."""
    with pytest.raises(ValueError, match="namespace"):
        CloudTagSet(
            install_slug="x",
            org_slug="x",
            app_slug="x",
            env_slug="x",
            extra={f"{PLATFORM_NAMESPACE}/sneaky": "value"},
        )


def test_canonical_dict_drops_empty_optional_fields():
    """Empty optionals shouldn't push ``key=""`` to the cloud —
    some billing reports treat empty values as a separate bucket
    from missing."""
    out = _base().as_canonical_dict()
    assert TAG_BINDING not in out
    assert TAG_AGENT_ID not in out
    # Required fields present
    assert out[TAG_INSTALL] == "acme-prod"
    assert out[TAG_ORG] == "acme"


def test_canonical_dict_includes_set_optionals():
    tags = CloudTagSet(
        install_slug="acme-prod",
        org_slug="acme",
        app_slug="api",
        env_slug="prod",
        binding_name="main_db",
        agent_id="agent-007",
        agent_run_id="run-42",
    )
    out = tags.as_canonical_dict()
    assert out[TAG_BINDING] == "main_db"
    assert out[TAG_AGENT_ID] == "agent-007"
    assert out[TAG_AGENT_RUN_ID] == "run-42"


def test_min_required_keys_locked():
    """Lock-test: changing what's mandatory needs to be a
    deliberate change reviewed by a human."""
    assert MIN_REQUIRED_KEYS == {
        TAG_INSTALL,
        TAG_ORG,
        TAG_APP,
        TAG_ENV,
    }


# ---- AWS serialization ---------------------------------------------


def test_aws_keeps_slash_in_keys():
    """AWS allows '/' in tag keys, so `astrolift.io/app` works
    as-is."""
    out = to_aws(_base())
    assert TAG_APP in out


def test_aws_truncates_long_values():
    tags = CloudTagSet(
        install_slug="acme-prod",
        org_slug="acme",
        app_slug="api",
        env_slug="prod",
        extra={"app/notes": "x" * 500},
    )
    out = to_aws(tags)
    assert len(out["app/notes"]) == 256


def test_aws_replaces_disallowed_chars():
    tags = CloudTagSet(
        install_slug="acme-prod",
        org_slug="acme",
        app_slug="api",
        env_slug="prod",
        extra={"app/notes": "value with $weird*chars"},
    )
    out = to_aws(tags)
    # $ and * become underscore; spaces are allowed.
    assert "$" not in out["app/notes"]
    assert "*" not in out["app/notes"]


# ---- GCP serialization ---------------------------------------------


def test_gcp_lowercases_and_replaces_dots_slashes():
    """GCP labels disallow '.' and '/' in keys. The canonical
    `astrolift.io/app` becomes `astrolift_io_app`."""
    out = to_gcp(_base())
    assert "astrolift_io_app" in out
    assert "astrolift_io_install" in out
    assert "astrolift.io/app" not in out


def test_gcp_lowercases_values():
    tags = CloudTagSet(
        install_slug="ACME-PROD",
        org_slug="Acme",
        app_slug="API",
        env_slug="Prod",
    )
    out = to_gcp(tags)
    assert out["astrolift_io_install"] == "acme-prod"


def test_gcp_truncates_to_63_chars():
    # Values longer than 63 chars get truncated
    long_tags = CloudTagSet(
        install_slug="acme",
        org_slug="acme",
        app_slug="api",
        env_slug="prod",
        extra={"team": "x" * 100},
    )
    out = to_gcp(long_tags)
    assert len(out["team"]) == 63


# ---- Azure serialization -------------------------------------------


def test_azure_preserves_keys_unchanged():
    """Azure allows mixed case + symbols in keys; platform tags
    pass through unchanged."""
    out = to_azure(_base())
    assert TAG_APP in out
    assert out[TAG_APP] == "api"


def test_azure_truncates_to_256():
    tags = CloudTagSet(
        install_slug="acme",
        org_slug="acme",
        app_slug="api",
        env_slug="prod",
        extra={"long": "x" * 500},
    )
    out = to_azure(tags)
    assert len(out["long"]) == 256


# ---- enforcement ---------------------------------------------------


def test_assert_required_passes_when_all_present():
    assert_required_tags(
        {
            TAG_INSTALL: "acme-prod",
            TAG_ORG: "acme",
            TAG_APP: "api",
            TAG_ENV: "prod",
        }
    )


def test_assert_required_fails_when_missing():
    """The CI test in astrolift-providers calls this on every
    freshly-provisioned resource. Catches plugin authors who
    forgot to thread tags through."""
    with pytest.raises(TagEnforcementError, match="missing required"):
        assert_required_tags(
            {
                TAG_INSTALL: "acme-prod",
                TAG_ORG: "acme",
                # missing TAG_APP and TAG_ENV
            }
        )


def test_assert_required_treats_empty_as_missing():
    with pytest.raises(TagEnforcementError):
        assert_required_tags(
            {
                TAG_INSTALL: "acme-prod",
                TAG_ORG: "",
                TAG_APP: "api",
                TAG_ENV: "prod",
            }
        )


# ---- agent stamping ------------------------------------------------


def test_for_agent_run_stamps_agent_fields():
    """Platform-run agent provisions cloud resources; the bill
    routes back to the agent run."""
    base = _base()
    stamped = for_agent_run(
        base=base,
        agent_id="agent-007",
        agent_run_id="run-42",
    )
    assert stamped.agent_id == "agent-007"
    assert stamped.agent_run_id == "run-42"
    # Original platform tags preserved
    assert stamped.app_slug == base.app_slug


def test_for_agent_run_requires_both_ids():
    base = _base()
    with pytest.raises(ValueError):
        for_agent_run(base=base, agent_id="", agent_run_id="run-1")
    with pytest.raises(ValueError):
        for_agent_run(base=base, agent_id="agent-1", agent_run_id="")


def test_agent_tags_appear_in_aws_output():
    base = _base()
    stamped = for_agent_run(
        base=base,
        agent_id="agent-007",
        agent_run_id="run-42",
    )
    out = to_aws(stamped)
    assert out[TAG_AGENT_ID] == "agent-007"
    assert out[TAG_AGENT_RUN_ID] == "run-42"
