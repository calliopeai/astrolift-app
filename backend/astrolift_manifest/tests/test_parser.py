"""Tests for the manifest TOML parser (#40, #114)."""

from __future__ import annotations

import pytest

from astrolift_manifest.parser import ManifestError, parse_raw


def test_parse_minimal_manifest():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""
    raw = parse_raw(toml)
    assert raw.name == "hello"
    assert len(raw.workloads) == 1
    w = raw.workloads[0]
    assert w.name == "web" and w.kind == "deployment"
    assert len(w.containers) == 1
    assert w.containers[0].name == "app" and w.containers[0].port == 8080


def test_parse_rejects_invalid_workload_kind():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "spaceship"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "kind" in str(exc.value).lower()


def test_parse_cronjob_requires_schedule():
    toml = """
name = "hello"

[[workloads]]
name = "nightly"
kind = "cronjob"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    # Path includes the offending key
    assert "schedule" in str(exc.value)


def test_parse_cronjob_with_schedule():
    toml = """
name = "hello"

[[workloads]]
name = "nightly"
kind = "cronjob"
schedule = "0 0 * * *"

  [[workloads.containers]]
  name = "job"
  is_primary = true
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].schedule == "0 0 * * *"


def test_parse_managed_service_block():
    toml = """
name = "hello"

[[managed_services]]
kind = "postgres"
name = "main_db"
variant = "small"

[[managed_services]]
kind = "redis"
"""
    raw = parse_raw(toml)
    assert len(raw.managed_services) == 2
    assert raw.managed_services[0].kind == "postgres"
    assert raw.managed_services[0].name == "main_db"
    assert raw.managed_services[0].variant == "small"


def test_parse_invalid_toml_surfaces_path():
    # Stray bracket — TOML decoder error
    with pytest.raises(ManifestError) as exc:
        parse_raw("name = [unterminated")
    assert "invalid TOML" in str(exc.value)


def test_invalid_toml_carries_line_and_column():
    """tomllib errors expose a position; we surface it on
    ManifestError so editors can highlight the offending row."""
    bad = "name = \"hello\"\n[[workloads]]\nkind = (\nname = \"x\"\n"
    with pytest.raises(ManifestError) as exc:
        parse_raw(bad)
    err = exc.value
    assert err.line is not None
    assert err.column is not None
    # The offending opening paren is on the 3rd line.
    assert err.line == 3


def test_locate_in_source_finds_leaf_key():
    """Best-effort locator for semantic errors (path → line/col)."""
    from astrolift_manifest.parser import locate_in_source

    text = (
        'name = "hello"\n'
        "\n"
        "[[workloads]]\n"
        'name = "web"\n'
        'kind = "spaceship"\n'
    )
    line, col = locate_in_source(text, "workloads[0].kind")
    assert line == 5
    assert col == 1


def test_locate_in_source_returns_none_when_missing():
    from astrolift_manifest.parser import locate_in_source

    line, col = locate_in_source('name = "hello"', "workloads[0].kind")
    assert line is None
    assert col is None


def test_parse_missing_name_is_error():
    toml = """
[[workloads]]
name = "web"
kind = "deployment"
"""
    with pytest.raises(ManifestError):
        parse_raw(toml)


def test_parse_healthcheck_kind_validation():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true

    [workloads.containers.healthcheck]
    kind = "telepathy"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "healthcheck" in str(exc.value).lower()
