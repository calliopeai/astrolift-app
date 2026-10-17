"""The skeleton of a new semantic layer and the host calls that land it (#2424).

No database: the edition is a zip built here, the host is a recorder in place
of the HTTP layer."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from astrolift_manifest.parser import parse_raw
from astrolift_registry import brain_template
from astrolift_registry.brain_substrate import Selection, app_slug, render_manifest
from astrolift_scm.providers import ProviderError, repo_writes

CONFIG = {
    "client": {"name": "Mimwell", "shortName": "MIMWELL", "slug": "mimwell", "domain": "localhost"},
    "brain": {"store": "json"},
    "deploy": {"target": "cloudflare-worker", "persistence": []},
}


def config_schema(*deploy_keys: str) -> bytes:
    """A Mimwell ``client.config.schema.json`` whose ``deploy`` takes exactly ``deploy_keys``."""
    deploy = {
        "type": "object",
        "additionalProperties": False,
        "properties": {key: {} for key in deploy_keys},
    }
    return json.dumps({"type": "object", "properties": {"deploy": deploy}}).encode()


#: The deploy keys of a Mimwell version before deploy.app_slug.
OLD_DEPLOY = ("target", "persistence", "replicas", "public", "image", "port")


def edition_zip(*, files=None, tamper=None, drop=None, extra=None, link=None) -> bytes:
    """A Mimwell edition archive under one top-level directory, the way a
    source archive arrives."""
    files = dict(
        files
        or {
            "README.md": b"# Mimwell\n",
            "client.config.json": json.dumps(CONFIG).encode(),
            brain_template.CONFIG_SCHEMA: config_schema(*OLD_DEPLOY, "app_slug"),
            "extensions/portal-container/Dockerfile": b"FROM nginx\n",
            "scripts/build.sh": b"#!/bin/sh\n",
            "assets/mark.png": b"\x89PNG\x00\x01binary",
            "astrolift.toml": b'name = "theirs"\n',
        }
    )
    listed = [
        {"path": p, "sha256": hashlib.sha256(c).hexdigest(), "mode": 493 if p.endswith(".sh") else 420}
        for p, c in files.items()
    ]
    if tamper:
        files[tamper] = files[tamper] + b"changed"
    if drop:
        files.pop(drop)
    edition = {"edition": "mimwell", "version": "0.4.0", "files": listed}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        bundle.writestr("mimwell-abc123/edition.json", json.dumps(edition))
        for path, content in {**files, **(extra or {})}.items():
            info = zipfile.ZipInfo(f"mimwell-abc123/{path}")
            if path == link:
                info.external_attr = 0o120777 << 16
            bundle.writestr(info, content)
    return out.getvalue()


def test_the_edition_is_kept_exactly_as_its_manifest_lists_it():
    edition = brain_template.read_edition(edition_zip(extra={"website/index.md": b"docs"}), ref="v0.4.0")
    paths = {f.path for f in edition.files}
    assert "website/index.md" not in paths  # not part of the edition
    assert {"README.md", "edition.json", brain_template.DOCKERFILE} <= paths
    assert edition.version == "0.4.0"
    assert next(f for f in edition.files if f.path == "scripts/build.sh").executable


@pytest.mark.parametrize(
    "kwargs, says",
    [
        ({"tamper": "README.md"}, "README.md (hash differs)"),
        ({"drop": "scripts/build.sh"}, "scripts/build.sh (missing)"),
        ({"link": "README.md"}, "README.md (a link)"),
    ],
)
def test_one_bad_file_refuses_the_whole_edition(kwargs, says):
    with pytest.raises(brain_template.TemplateError) as exc:
        brain_template.read_edition(edition_zip(**kwargs), ref="main")
    assert exc.value.code == "PRECONDITION"
    assert says in exc.value.message


def test_an_edition_without_the_container_dockerfile_needs_an_image():
    files = {"client.config.json": json.dumps(CONFIG).encode(), "README.md": b"x"}
    edition = brain_template.read_edition(edition_zip(files=files), ref="main")
    with pytest.raises(brain_template.TemplateError, match="extensions/portal-container/Dockerfile"):
        brain_template.scaffold(edition, slug="acme-brain", name="Acme", selection=Selection())
    given = brain_template.scaffold(
        edition, slug="acme-brain", name="Acme", selection=Selection(image="ghcr.io/a/b:1")
    )
    assert "image_ref" in {f.path: f.content for f in given}["astrolift.toml"].decode()


def test_the_scaffold_writes_only_the_brains_identity_and_its_manifest():
    edition = brain_template.read_edition(edition_zip(), ref="v0.4.0")
    selection = Selection(authority="postgres", persistence=("postgres", "sqlite-volume"), replicas=2)
    files = {
        f.path: f.content
        for f in brain_template.scaffold(
            edition, slug="acme-brain", name="Acme Knowledge", selection=selection
        )
    }
    config = json.loads(files["client.config.json"])
    assert config["brain"] == {"store": "json", "name": "Acme Knowledge", "shortName": "ACME"}
    assert config["client"]["slug"] == "acme"  # Mimwell names the app <client.slug>-brain
    assert config["client"]["domain"] == "localhost"  # unknown until served
    assert config["deploy"] == {
        "target": "astrolift",
        "persistence": ["postgres", "sqlite-volume"],
        "replicas": 2,
        "port": 8080,
    }
    assert config["semantic"] == {"substrate": "local"}
    assert files["README.md"] == b"# Mimwell\n"
    raw = parse_raw(files["astrolift.toml"].decode())
    assert raw.app_kind == "mimwell"
    [workload] = raw.raw["workloads"]
    assert workload["name"] == "brain" and workload["is_public"] is True
    assert workload["kind"] == "statefulset" and workload["replicas"] == 2
    [container] = workload["containers"]
    assert container["dockerfile_path"] == brain_template.DOCKERFILE
    assert raw.raw["managed_services"][0]["kind"] == "postgres"
    assert container["port"] == 8080 and container["healthcheck"]["value"] == "/health"


def _scaffolded(edition, slug):
    files = {
        f.path: f.content
        for f in brain_template.scaffold(edition, slug=slug, name="Acme", selection=Selection())
    }
    return json.loads(files["client.config.json"]), parse_raw(files["astrolift.toml"].decode()).raw


def test_a_slug_without_brain_is_written_as_deploy_app_slug():
    edition = brain_template.read_edition(edition_zip(), ref="v0.4.0")
    config, manifest = _scaffolded(edition, "acme")
    assert config["client"]["slug"] == "acme" and config["deploy"]["app_slug"] == "acme"
    assert app_slug(config) == "acme"  # the brain's own make astrolift-manifest keeps the name
    assert (manifest["name"], manifest["app"]["slug"]) == ("acme", "acme")
    config, manifest = _scaffolded(edition, "acme-brain")
    assert config["client"]["slug"] == "acme" and "app_slug" not in config["deploy"]
    assert (manifest["name"], manifest["app"]["slug"]) == ("acme-brain", "acme-brain")


@pytest.mark.parametrize(
    "schema", [config_schema(*OLD_DEPLOY), None, b"not json"], ids=["older-schema", "no-schema", "not-json"]
)
def test_an_edition_without_deploy_app_slug_takes_only_a_brain_slug(schema):
    files = {
        "client.config.json": json.dumps(CONFIG).encode(),
        brain_template.DOCKERFILE: b"FROM nginx\n",
        **({brain_template.CONFIG_SCHEMA: schema} if schema is not None else {}),
    }
    edition = brain_template.read_edition(edition_zip(files=files), ref="v0.3.4")
    with pytest.raises(brain_template.TemplateError) as exc:
        _scaffolded(edition, "acme")
    assert exc.value.code == "PRECONDITION"
    assert "has no deploy.app_slug" in exc.value.message and "(acme-brain)" in exc.value.message
    config, manifest = _scaffolded(edition, "acme-brain")
    assert "app_slug" not in config["deploy"] and manifest["name"] == "acme-brain"


def test_a_name_cannot_break_out_of_the_manifest():
    manifest = render_manifest(Selection(), slug="kb-brain", display_name='x"\n[evil]\nkind = "generic')
    raw = parse_raw(manifest)
    assert raw.app_kind == "mimwell"
    assert "evil" not in raw.raw


@pytest.mark.parametrize("ref", ["", "../etc", "a b", "-x", "a..b"])
def test_a_template_ref_is_a_plain_name(ref):
    with pytest.raises(brain_template.TemplateError):
        brain_template.validate_ref(ref)


# ---------------------------------------------------------------------------
# Host calls
# ---------------------------------------------------------------------------


class Recorder:
    """Stands in for ``repo_writes._http``: answers by (method, url suffix)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method, url, headers, *, body=None, raw=None, timeout=20, host=""):
        self.calls.append((method, url, body if body is not None else raw))
        for (m, suffix), answer in self.answers.items():
            if m == method and url.split("?")[0].endswith(suffix):
                if isinstance(answer, Exception):
                    raise answer
                return 201, answer, SimpleNamespace(headers={}, url="")
        raise AssertionError(f"unexpected {method} {url}")


def gh(kind="github_pat"):
    return SimpleNamespace(kind=kind)


@pytest.fixture
def github(monkeypatch):
    monkeypatch.setattr(repo_writes, "_gh", lambda c: ("https://api.github.test", {}))


def test_an_app_installation_cannot_create_a_repository(github):
    with pytest.raises(ProviderError) as exc:
        repo_writes.create_repository(gh("github_app_install"), "acme", "kb", "private")
    assert exc.value.code == "PRECONDITION"
    assert "create the empty repository first" in exc.value.message


def test_an_empty_github_repository_gets_one_tree_commit_on_a_seeded_root(github, monkeypatch):
    rec = Recorder(
        {
            ("PUT", "/contents/README.md"): {"commit": {"sha": "seed"}},
            ("POST", "/git/blobs"): {"sha": "blob1"},
            ("POST", "/git/trees"): {"sha": "tree1"},
            ("POST", "/git/commits"): {"sha": "c0ffee"},
            ("PATCH", "/git/refs/heads/main"): {},
        }
    )
    monkeypatch.setattr(repo_writes, "_http", rec)
    files = [
        repo_writes.TreeFile("README.md", b"# kb\n"),
        repo_writes.TreeFile("assets/mark.png", b"\x00\x01"),
        repo_writes.TreeFile("run.sh", b"#!/bin/sh\n", executable=True),
    ]
    sha = repo_writes.commit_tree(
        gh(),
        "acme/kb",
        "main",
        files,
        "Create",
        state=repo_writes.RepoState(exists=True, empty=True),
        author={"name": "Reba", "email": "reba@acme.test"},
    )
    assert sha == "c0ffee"
    [tree] = [body for method, url, body in rec.calls if url.endswith("/git/trees")]
    assert "base_tree" not in tree
    entries = {e["path"]: e for e in tree["tree"]}
    assert entries["assets/mark.png"]["sha"] == "blob1"  # binary goes as a blob
    assert entries["run.sh"]["mode"] == "100755" and entries["README.md"]["content"] == "# kb\n"
    [commit] = [body for method, url, body in rec.calls if url.endswith("/git/commits")]
    assert commit["parents"] == ["seed"] and commit["author"]["email"] == "reba@acme.test"
    [ref] = [body for method, url, body in rec.calls if method == "PATCH"]
    assert ref == {"sha": "c0ffee", "force": False}


def test_a_large_tree_chains_its_requests_through_base_tree(github, monkeypatch):
    trees = iter(["t1", "t2", "t3"])
    rec = Recorder({("POST", "/git/commits"): {"sha": "c1"}, ("PATCH", "/git/refs/heads/main"): {}})
    original = rec.__call__

    def call(method, url, headers, **kw):
        if url.endswith("/git/trees"):
            rec.calls.append((method, url, kw.get("body")))
            return 201, {"sha": next(trees)}, None
        return original(method, url, headers, **kw)

    monkeypatch.setattr(repo_writes, "_http", call)
    files = [repo_writes.TreeFile(f"f{i}.txt", b"x") for i in range(250)]
    repo_writes.commit_tree(
        gh(),
        "acme/kb",
        "main",
        files,
        "m",
        state=repo_writes.RepoState(exists=True, empty=True, head_sha="readme"),
    )
    bodies = [body for _, url, body in rec.calls if url.endswith("/git/trees")]
    assert [len(b["tree"]) for b in bodies] == [100, 100, 50]
    assert [b.get("base_tree") for b in bodies] == [None, "t1", "t2"]


def test_a_repository_with_history_is_never_written(github):
    with pytest.raises(ProviderError) as exc:
        repo_writes.commit_tree(
            gh(),
            "acme/kb",
            "main",
            [repo_writes.TreeFile("a", b"b")],
            "m",
            state=repo_writes.RepoState(exists=True, empty=False, head_sha="h"),
        )
    assert exc.value.code == "CONFLICT"


def test_a_readme_only_github_repository_reads_as_empty(github, monkeypatch):
    monkeypatch.setattr(
        repo_writes,
        "_http",
        Recorder(
            {
                ("GET", "/repos/acme/kb"): {"default_branch": "trunk"},
                ("GET", "/repos/acme/kb/commits"): [{"sha": "r1"}],
                ("GET", "/git/trees/r1"): {"tree": [{"path": "README.md", "sha": "b"}]},
            }
        ),
    )
    state = repo_writes.repository_state(gh(), "acme/kb")
    assert state.exists and state.empty and state.head_sha == "r1" and state.default_branch == "trunk"


def test_gitlab_lands_the_tree_as_one_commit_replacing_its_readme(monkeypatch):
    monkeypatch.setattr(repo_writes, "_gl", lambda c: ("https://gitlab.test", {}))
    rec = Recorder({("POST", "/repository/commits"): {"id": "g1"}})
    monkeypatch.setattr(repo_writes, "_http", rec)
    sha = repo_writes.commit_tree(
        SimpleNamespace(kind="gitlab_pat"),
        "acme/kb",
        "main",
        [repo_writes.TreeFile("README.md", b"r"), repo_writes.TreeFile("a.txt", b"a")],
        "m",
        state=repo_writes.RepoState(exists=True, empty=True, head_sha="h", files=("README.md",)),
    )
    assert sha == "g1"
    [(_, _, body)] = rec.calls
    assert {(a["action"], a["file_path"]) for a in body["actions"]} == {
        ("update", "README.md"),
        ("create", "a.txt"),
    }


def test_an_unknown_visibility_is_refused_before_any_call():
    with pytest.raises(ProviderError) as exc:
        repo_writes.create_repository(gh(), "acme", "kb", "secret")
    assert exc.value.code == "VALIDATION"
