"""Astrolift's port of Mimwell's substrate model stays Mimwell's (#2424).

The fixtures are the real outputs at the pinned commit
(``brain_substrate.MIMWELL_SOURCE``): every cell from
``knowledge_store.cell_status()`` and ``scripts/gen-astrolift.py``'s
``render()`` for a set of configs. Regenerate them from mimwell-pvt when the
pin moves; a drift here means the port has to follow."""

from __future__ import annotations

import json
import pathlib
import tomllib

import pytest

from astrolift_manifest.parser import parse_raw
from astrolift_registry import brain_substrate as bs

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_every_cell_has_mimwells_status():
    data = _load("mimwell_cells.json")
    assert bs.MIMWELL_SOURCE.split()[-1] in data["source"]
    assert len(data["cells"]) == 192
    for row in data["cells"]:
        cell = {axis: row[axis] for axis in bs.AXES}
        assert bs.cell_status(cell) == (row["status"], row["reason"]), bs.cell_name(cell)


@pytest.mark.parametrize("case", sorted(_load("mimwell_gen_astrolift.json")["cases"]))
def test_the_manifest_follows_gen_astrolift(case):
    data = _load("mimwell_gen_astrolift.json")
    assert bs.MIMWELL_SOURCE.split()[-1] in data["source"]
    fixture = data["cases"][case]
    deploy = fixture["config"]["deploy"]
    graph = next(
        (s["driver"] for s in (fixture["contract"] or {}).get("stores", ()) if "graph" in s["roles"]),
        "edges-table",
    )
    selection = bs.Selection(
        authority="postgres" if graph == "age" else "files",
        graph=graph,
        persistence=tuple(deploy.get("persistence") or ()),
        replicas=deploy.get("replicas") or 1,
        image=deploy["image"],
        port=deploy.get("port") or 8080,
    )
    ours = bs.render_manifest(selection, slug="acme-brain", display_name="Acme")
    theirs = tomllib.loads(fixture["toml"])
    mine = tomllib.loads(ours)
    for key in ("astrolift_version", "name", "environments", "workloads"):
        assert mine[key] == theirs[key], key
    assert mine.get("managed_services") == theirs.get("managed_services")
    assert mine["app"]["slug"] == theirs["app"]["slug"]
    assert mine["app"]["kind"] == "mimwell"  # the line gen-astrolift.py does not write yet
    assert ("s3: object storage" in ours) == ("s3" in selection.persistence)
    parse_raw(ours)  # and Astrolift's own parser accepts it


def test_without_an_image_the_container_builds_the_edition_dockerfile():
    raw = tomllib.loads(bs.render_manifest(bs.Selection(), slug="acme-brain", display_name="Acme"))
    [container] = raw["workloads"][0]["containers"]
    assert container["dockerfile_path"] == bs.DOCKERFILE and "image_ref" not in container


@pytest.mark.parametrize(
    "selection, says",
    [
        (
            bs.Selection(authority="d1"),
            "unsupported cell: authority=d1, blobs=fs, vectors=local, graph=edges-table",
        ),
        (bs.Selection(blobs="db-blob"), "unsupported cell: authority=files, blobs=db-blob"),
        (bs.Selection(vectors="in-db"), "in-db vectors live in the authority database and files has none"),
        (
            bs.Selection(authority="sqlite", graph="age", persistence=("sqlite-volume",)),
            "age is a Postgres extension",
        ),
        (bs.Selection(authority="postgres"), "authority=postgres needs persistence 'postgres'"),
        (bs.Selection(authority="sqlite"), "authority=sqlite needs persistence 'sqlite-volume'"),
        (bs.Selection(blobs="s3"), "blobs=s3 needs persistence 's3'"),
        (bs.Selection(persistence=("d1",)), "belongs to the Worker path"),
        (bs.Selection(replicas=0), "replicas must be between 1"),
    ],
)
def test_a_cell_that_cannot_be_hosted_is_refused_by_name(selection, says):
    with pytest.raises(bs.SubstrateError) as exc:
        bs.validate(selection)
    assert says in exc.value.message


def test_a_supported_cell_validates_and_drops_none():
    selection = bs.validate(
        bs.Selection(
            authority="postgres", vectors="in-db", graph="age", persistence=("none", "postgres", "postgres")
        )
    )
    assert selection.persistence == ("postgres",)


def test_dropping_persistence_that_holds_data_is_named():
    before = bs.Selection(authority="postgres", persistence=("postgres", "sqlite-volume", "s3"))
    after = bs.Selection(persistence=("s3",))
    assert bs.dropped_data(before, after) == ["postgres", "sqlite-volume"]


def test_the_slug_is_the_client_slug_plus_brain():
    assert bs.client_slug("leo-brain") == "leo"
    with pytest.raises(bs.SubstrateError):
        bs.client_slug("leo")


def test_a_merge_keeps_what_the_brain_declares_beyond_the_substrate():
    current = """astrolift_version = 1
name = "leo-brain"

[app]
slug = "leo-brain"
display_name = "MAIN BRAIN"

[environments.production]

[[workloads]]
name = "brain"
kind = "deployment"
replicas = 1

[[workloads]]
name = "sidecar"
kind = "deployment"

[ingress]
class = "alb"
"""
    rendered = bs.render_manifest(
        bs.Selection(persistence=("sqlite-volume",), replicas=2), slug="leo-brain", display_name="Leo"
    )
    merged = tomllib.loads(bs.merge_manifest(current, rendered))
    assert merged["ingress"] == {"class": "alb"}
    assert [w["name"] for w in merged["workloads"]] == ["brain", "sidecar"]
    assert merged["workloads"][0]["kind"] == "statefulset"
    assert merged["app"]["kind"] == "mimwell"


def test_the_config_takes_the_selection_where_mimwell_reads_it():
    config = bs.apply_to_config(
        {"brain": {"store": "json"}, "deploy": {"target": "cloudflare-worker"}},
        bs.Selection(blobs="s3", vectors="edge-db", persistence=("s3", "efs"), replicas=3),
        slug="acme-brain",
    )
    assert config["brain"]["blobs"] == "s3"
    assert config["semantic"] == {"substrate": "edge-db"}
    assert config["deploy"] == {
        "target": "astrolift",
        "persistence": ["s3", "efs"],
        "replicas": 3,
        "port": 8080,
    }
    assert "authority" not in config["brain"]  # adoption is the brain's own cutover
