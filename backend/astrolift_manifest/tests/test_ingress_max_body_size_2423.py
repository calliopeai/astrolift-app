"""``[ingress] max_body_size``: the largest request body an app accepts (#2423).

The value becomes the app Ingress's ``proxy-body-size``, so it is held to what
nginx reads as a size. A malformed size is dropped or refused by ingress-nginx,
and one too large for nginx fails the configuration test of the whole
controller, every app's; the parser refuses both before anything renders.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import ManifestError, parse_raw

_BASE = """
name = "abbott-cgm"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true
"""


def _with(value: str) -> str:
    return _BASE + f"\n[ingress]\nmax_body_size = {value}\n"


def test_a_manifest_without_it_parses_to_empty():
    assert parse_raw(_BASE).ingress_max_body_size == ""


def test_the_other_ingress_keys_do_not_set_it():
    manifest = parse_raw(_BASE + '\n[ingress]\nclass = "nginx"\n\n[ingress.access]\ngroups = ["ops"]\n')

    assert manifest.ingress_max_body_size == ""
    assert manifest.ingress_access == {"groups": ["ops"], "users": []}


@pytest.mark.parametrize(
    "value",
    ["0", "100m", "512k", "2G", "1048576", "9223372036854775807", "8589934591g"],
)
def test_a_size_nginx_reads_is_kept(value):
    assert parse_raw(_with(f'"{value}"')).ingress_max_body_size == value


def test_surrounding_whitespace_is_dropped():
    assert parse_raw(_with('" 64m "')).ingress_max_body_size == "64m"


@pytest.mark.parametrize(
    "value",
    [
        '"100mb"',
        '"1.5m"',
        '"-1"',
        '""',
        '"100 m"',
        '"1t"',
        '"unlimited"',
        # nginx reads the size into an off_t; one past it fails the controller.
        '"9223372036854775808"',
        '"8589934592g"',
        # Arabic-Indic digits: a \d pattern would take them, nginx would not.
        '"' + chr(0x661) + chr(0x660) * 2 + 'm"',
        "0",
        "100",
        "true",
    ],
)
def test_anything_else_is_refused(value):
    with pytest.raises(ManifestError, match="max_body_size must be a size") as raised:
        parse_raw(_with(value))
    assert raised.value.path == "ingress.max_body_size"


def test_a_manifest_without_it_serializes_unchanged():
    """No key at all, so no existing app's manifest hash moves and nothing
    redeploys on the upgrade alone."""
    assert "ingress_max_body_size" not in normalize(parse_raw(_BASE)).serialized


def test_a_declared_size_survives_normalization():
    assert normalize(parse_raw(_with('"100m"'))).serialized["ingress_max_body_size"] == "100m"
    assert normalize(parse_raw(_with('"0"'))).serialized["ingress_max_body_size"] == "0"
