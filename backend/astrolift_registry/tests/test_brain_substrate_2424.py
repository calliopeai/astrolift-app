"""Astrolift's port of Mimwell's substrate model stays Mimwell's (#2424).

The fixtures are the real outputs at the pinned commits
(``brain_substrate.MIMWELL_SOURCE``): every cell from
``knowledge_store.cell_status()`` and ``scripts/gen-astrolift.py``'s
``render()`` for a set of configs, and (``MIMWELL_APP_SLUG_SOURCE``) its
``render()`` of a config with ``deploy.app_slug`` and its slug rule.
Regenerate them from mimwell-pvt when a pin moves; a drift here means the
port has to follow."""

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


#: Each gen-astrolift.py fixture and the pin it was rendered at: the configs
#: without deploy.app_slug at the substrate pin, the one with it at its own.
RENDERS = {
    "mimwell_gen_astrolift.json": bs.MIMWELL_SOURCE,
    "mimwell_gen_astrolift_app_slug.json": bs.MIMWELL_APP_SLUG_SOURCE,
}


@pytest.mark.parametrize(
    "name, case", [(name, case) for name in RENDERS for case in sorted(_load(name)["cases"])]
)
def test_the_manifest_follows_gen_astrolift(name, case):
    data = _load(name)
    assert RENDERS[name].split()[-1] in data["source"]
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
    ours = bs.render_manifest(selection, slug=bs.app_slug(fixture["config"]), display_name="Acme")
    theirs = tomllib.loads(fixture["toml"])
    mine = tomllib.loads(ours)
    # Memory is Astrolift's own (#2441); gen-astrolift.py leaves it to the host.
    for workload in mine["workloads"]:
        assert (workload.pop("memory_request"), workload.pop("memory_limit")) == ("128Mi", "512Mi")
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


def test_the_slug_rule_is_gen_astrolifts():
    data = _load("mimwell_gen_astrolift_app_slug.json")
    assert bs.APP_SLUG.pattern == data["app_slug_pattern"]


@pytest.mark.parametrize(
    "slug, client, app_slug",
    [
        ("leo-brain", "leo", None),
        ("a-brain", "a", None),
        ("leo", "leo", "leo"),
        ("acme-kb", "acme-kb", "acme-kb"),
        ("brain", "brain", "brain"),
        ("brainy", "brainy", "brainy"),
    ],
)
def test_any_slug_is_written_so_mimwell_names_the_app_by_it(slug, client, app_slug):
    config = bs.apply_to_config(
        {"client": {"slug": "example", "domain": "localhost"}, "deploy": {"target": "cloudflare-worker"}},
        bs.Selection(),
        slug=slug,
    )
    assert config["client"] == {"slug": client, "domain": "localhost"}
    assert config["deploy"].get("app_slug") == app_slug
    assert bs.app_slug(config) == slug


def test_a_config_that_already_names_the_app_keeps_its_client_slug():
    pinned = {"client": {"slug": "acme"}, "deploy": {"app_slug": "kb", "target": "astrolift"}}
    config = bs.apply_to_config(pinned, bs.Selection(), slug="kb")
    assert (config["client"], config["deploy"]["app_slug"]) == ({"slug": "acme"}, "kb")
    default = bs.apply_to_config({"client": {"slug": "acme"}}, bs.Selection(), slug="acme-brain")
    assert default["client"] == {"slug": "acme"} and "app_slug" not in default["deploy"]
    # A stale app_slug would rename the app: a -brain slug drops it.
    stale = {"client": {"slug": "old"}, "deploy": {"app_slug": "old"}}
    config = bs.apply_to_config(stale, bs.Selection(), slug="acme-brain")
    assert config["client"] == {"slug": "acme"} and "app_slug" not in config["deploy"]
    assert bs.app_slug(config) == "acme-brain"


def test_a_slug_that_is_no_app_slug_is_never_written_as_one():
    with pytest.raises(bs.SubstrateError) as exc:
        bs.apply_to_config({}, bs.Selection(), slug="1acme")
    assert exc.value.field == "slug" and "deploy.app_slug" in exc.value.message
    # A registered -brain slug needs no app_slug, as before.
    assert bs.apply_to_config({}, bs.Selection(), slug="1acme-brain")["client"] == {"slug": "1acme"}


@pytest.mark.parametrize("value", ["", "Acme", "1acme", "-acme", "acme-", "acme_kb", "a" * 64, 5])
def test_app_slug_refuses_what_gen_astrolift_refuses(value):
    with pytest.raises(bs.SubstrateError):
        bs.app_slug({"client": {"slug": "acme"}, "deploy": {"app_slug": value}})


def test_app_slug_defaults_like_gen_astrolift():
    assert bs.app_slug({}) == "brain-brain"
    assert bs.app_slug({"client": {"slug": ""}, "deploy": {"app_slug": None}}) == "brain-brain"
    assert bs.app_slug({"client": {"slug": "acme"}}) == "acme-brain"
    assert bs.app_slug({"client": {"slug": "acme"}, "deploy": {"app_slug": "a" * 63}}) == "a" * 63


def test_only_a_schema_that_declares_app_slug_takes_it():
    assert bs.declares_app_slug({"properties": {"deploy": {"properties": {"app_slug": {"type": "string"}}}}})
    for schema in (None, [], {}, {"properties": {"deploy": {"properties": {"image": {}}}}}):
        assert not bs.declares_app_slug(schema)
    assert bs.adds_app_slug({}, {"deploy": {"app_slug": "kb"}})
    assert bs.adds_app_slug({"deploy": {"app_slug": "old"}}, {"deploy": {"app_slug": "kb"}})
    assert not bs.adds_app_slug({"deploy": {"app_slug": "kb"}}, {"deploy": {"app_slug": "kb"}})
    assert not bs.adds_app_slug({"deploy": {"app_slug": "kb"}}, {"deploy": {}})


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
