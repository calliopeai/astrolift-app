"""Tests for the platform feature-flag system (#134)."""

from __future__ import annotations

import os
from unittest.mock import patch

from config.features import (
    Feature,
    filter_installed_apps,
    get_disabled_apps,
    get_enabled_features,
    is_enabled,
)


def test_default_features_enabled():
    """All features default to True so omitting envvars yields a
    fully-functional platform."""
    with patch.dict(os.environ, {}, clear=False):
        for f in Feature:
            os.environ.pop(f.value.upper().replace("FEATURE_", "").replace("FEATURE", ""), None)
        # Just check the defaults map is consistent
        for f in Feature:
            assert is_enabled(f) is True


def test_explicit_disable():
    with patch.dict(os.environ, {"FEATURE_WORKFLOWS": "false"}):
        assert is_enabled(Feature.WORKFLOWS) is False
    with patch.dict(os.environ, {"FEATURE_WORKFLOWS": "0"}):
        assert is_enabled(Feature.WORKFLOWS) is False


def test_string_truthiness_variants():
    """Common truthy strings should be recognised so operators don't
    have to remember exactly which form to use."""
    for v in ("true", "1", "yes", "TRUE", "Yes"):
        with patch.dict(os.environ, {"FEATURE_WORKFLOWS": v}):
            assert is_enabled(Feature.WORKFLOWS) is True


def test_get_enabled_features_returns_each_flag():
    snap = get_enabled_features()
    # Every Feature enum value is represented
    assert set(snap.keys()) == set(Feature)


def test_filter_installed_apps_drops_disabled():
    apps = ["foo", "workflows", "bar"]
    with patch.dict(os.environ, {"FEATURE_WORKFLOWS": "false"}):
        # `workflows` Django app should be dropped when the feature
        # is disabled.
        result = filter_installed_apps(apps)
        assert "workflows" not in result
        assert "foo" in result and "bar" in result


def test_filter_installed_apps_passes_through_when_enabled():
    apps = ["foo", "workflows", "bar"]
    with patch.dict(os.environ, {"FEATURE_WORKFLOWS": "true"}):
        result = filter_installed_apps(apps)
        assert result == apps


def test_get_disabled_apps_is_set():
    with patch.dict(os.environ, {"FEATURE_WORKFLOWS": "false"}):
        disabled = get_disabled_apps()
        assert isinstance(disabled, set)
        assert "workflows" in disabled
