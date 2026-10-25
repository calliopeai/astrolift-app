"""Pinned public data consumption; no private template import or status port."""

import hashlib
import itertools
import json
import os
from copy import deepcopy

import pytest

from astrolift_registry import brain_substrate as bs


def _published():
    raw = bs.SUBSTRATE_TABLE_PATH.read_bytes()
    assert len(raw) == 43198
    assert hashlib.sha256(raw).hexdigest() == (
        "0c30ad508da54934cb42787f137c28d5e4aebbbb9c3199ae32ac29e560acfcb5"
    )
    return raw, json.loads(raw)


def test_exact_released_public_contract_drives_all_192_statuses_and_reasons():
    raw, document = _published()
    assert bs.MIMWELL_SOURCE == (
        "ConflictHQ/mimwell v0.3.6 e59de019b3d10564fdc1301caef06842ab84256d"
    )
    assert document["schemaVersion"] == 1
    assert document["axes"] == {axis: list(values) for axis, values in bs.AXES.items()}
    loaded = bs._load_substrate_cells(bs.SUBSTRATE_TABLE_PATH)
    expected = {
        tuple(row["cell"][axis] for axis in bs.AXES): (row["status"], row["reason"])
        for row in document["cells"]
    }
    assert len(document["cells"]) == len(expected) == len(loaded) == 192
    assert set(expected) == set(itertools.product(*bs.AXES.values()))
    for key, status in expected.items():
        assert loaded[key] == status
        assert bs.cell_status(dict(zip(bs.AXES, key, strict=True))) == status
    assert dict(bs._parse_substrate_cells(raw)) == expected
    with pytest.raises(TypeError):
        loaded[next(iter(loaded))] = ("supported", "request cannot alter pinned data")


def test_original_public_license_and_notice_are_shipped_unmodified():
    root = bs.SUBSTRATE_TABLE_PATH.parent
    assert hashlib.sha256((root / "LICENSE").read_bytes()).hexdigest() == (
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
    )
    assert hashlib.sha256((root / "NOTICE").read_bytes()).hexdigest() == (
        "266c00a092d82b40777148360c5ed6196cea102debe9c8491d11a32bc9d6a933"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "version-bool",
        "version-unknown",
        "unknown-top-key",
        "missing-axis",
        "unknown-axis-value",
        "missing-cell",
        "duplicate-cell",
        "extra-cell",
        "unknown-cell-key",
        "unknown-cell-axis",
        "unknown-status",
        "non-string-reason",
        "overlong-reason",
    ],
)
def test_malformed_or_incomplete_contracts_refuse_without_a_local_fallback(mutation):
    _raw, original = _published()
    value = deepcopy(original)
    if mutation == "version-bool":
        value["schemaVersion"] = True
    elif mutation == "version-unknown":
        value["schemaVersion"] = 2
    elif mutation == "unknown-top-key":
        value["fallback"] = "supported"
    elif mutation == "missing-axis":
        del value["axes"]["graph"]
    elif mutation == "unknown-axis-value":
        value["axes"]["authority"].append("invented-driver")
    elif mutation == "missing-cell":
        value["cells"].pop()
    elif mutation == "duplicate-cell":
        value["cells"][-1] = deepcopy(value["cells"][0])
    elif mutation == "extra-cell":
        value["cells"].append(deepcopy(value["cells"][0]))
    elif mutation == "unknown-cell-key":
        value["cells"][0]["permission"] = "granted"
    elif mutation == "unknown-cell-axis":
        value["cells"][0]["cell"]["authority"] = "invented-driver"
    elif mutation == "unknown-status":
        value["cells"][0]["status"] = "available"
    elif mutation == "non-string-reason":
        value["cells"][0]["reason"] = None
    elif mutation == "overlong-reason":
        value["cells"][0]["reason"] = "x" * 1025
    with pytest.raises(bs.SubstrateError) as refused:
        bs._parse_substrate_cells(json.dumps(value).encode())
    assert refused.value.field == "authority"


@pytest.mark.parametrize(
    "raw", [b"{", b'{"schemaVersion":1,"schemaVersion":1}', b'{"cells":NaN}', b"\xff"]
)
def test_invalid_json_duplicate_keys_constants_and_encoding_refuse(raw):
    with pytest.raises(bs.SubstrateError):
        bs._parse_substrate_cells(raw)


def test_loader_accepts_only_exact_published_bytes_and_refuses_missing_or_oversized_assets(tmp_path):
    raw, _document = _published()
    copied = tmp_path / "contract.json"
    copied.write_bytes(raw)
    assert dict(bs._load_substrate_cells(copied)) == dict(
        bs._load_substrate_cells(bs.SUBSTRATE_TABLE_PATH)
    )
    copied.write_bytes(raw + b"\n")
    with pytest.raises(bs.SubstrateError, match="unpinned"):
        bs._load_substrate_cells(copied)
    copied.unlink()
    with pytest.raises(bs.SubstrateError, match="unavailable"):
        bs._load_substrate_cells(copied)
    with copied.open("wb") as stream:
        stream.truncate(bs.MAX_SUBSTRATE_TABLE_BYTES + 1)
    with pytest.raises(bs.SubstrateError, match="unavailable"):
        bs._load_substrate_cells(copied)


def test_loader_refuses_non_regular_or_substituted_package_entries(tmp_path):
    actual = tmp_path / "actual.json"
    actual.write_bytes(_published()[0])
    symbolic = tmp_path / "symbolic.json"
    symbolic.symlink_to(actual)
    directory = tmp_path / "directory.json"
    directory.mkdir()
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo, 0o600)
    for path in (symbolic, directory, fifo):
        with pytest.raises(bs.SubstrateError, match="unavailable"):
            bs._load_substrate_cells(path)


@pytest.mark.parametrize(
    "cell",
    [{}, {**bs.DEFAULT_CELL, "unknown": "supported"}, {**bs.DEFAULT_CELL, "authority": "unknown"}],
)
def test_queries_cannot_invent_a_cell_or_a_fallback(cell):
    with pytest.raises(bs.SubstrateError):
        bs.cell_status(cell)


@pytest.mark.parametrize("authority", [{"store": "canon"}, None])
@pytest.mark.parametrize("declaration", ["authority", "collectionAuthority"])
def test_direct_config_rewrite_refuses_explicit_native_authority_without_mutating_input(
    authority, declaration
):
    config = {"brain": {declaration: authority}, "deploy": {"persistence": ["sqlite-volume"]}}
    original = deepcopy(config)
    with pytest.raises(bs.SubstrateError, match="owner-reviewed native manifest") as refused:
        bs.apply_to_config(config, bs.Selection(replicas=3), slug="acme-brain")
    assert refused.value.field == "authority"
    assert config == original
