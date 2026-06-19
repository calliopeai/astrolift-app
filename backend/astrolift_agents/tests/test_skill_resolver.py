"""Unit tests for the skill resolver (spec 38 Phase 3 + spec 39c).

Pure over fixture ``{path: contents}`` trees — the local/named/missing
resolution branches and the slug derivation don't touch the database. The
unauthenticated catalogue fetch is exercised against a stubbed
``requests.get`` (an in-memory zipball) so the public-zipball path runs without
a real GitHub call.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from astrolift_agents.services.skill_resolver import (
    CatalogueFetchError,
    SkillResolutionError,
    catalogue_ref,
    catalogue_repo,
    load_catalogue_tree,
    resolve_org_repo_skill,
    resolve_skill,
)
from astrolift_manifest.skills import SkillError
from astrolift_manifest.types import SkillRef


def _skill_md(name: str, description: str, body: str = "do the thing") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\n---\n\n{body}\n'


def _agent_tree() -> dict[str, str]:
    return {"skills/reviewer/SKILL.md": _skill_md("Reviewer", "reviews code")}


def _catalogue_tree() -> dict[str, str]:
    return {"skills/pr-review/SKILL.md": _skill_md("PR Review", "reviews PRs")}


# ---- resolve_skill: local ---------------------------------------------


def test_resolve_local_skill_loads_from_agent_repo():
    ref = SkillRef(name="reviewer", path="skills/reviewer", kind="local")
    loaded = resolve_skill(ref, agent_repo_tree=_agent_tree(), catalogue_tree={})
    assert loaded.name == "Reviewer"
    assert loaded.description == "reviews code"
    assert loaded.instructions == "do the thing"


def test_resolve_local_skill_missing_path_raises():
    ref = SkillRef(name="ghost", path="skills/ghost", kind="local")
    with pytest.raises(SkillError):  # subclass of SkillResolutionError's base ManifestError
        resolve_skill(ref, agent_repo_tree=_agent_tree(), catalogue_tree={})


def test_resolve_local_skill_without_path_raises_resolution_error():
    # Defensive branch: a local ref with no path can't be located.
    ref = SkillRef(name="reviewer", path=None, kind="local")
    with pytest.raises(SkillResolutionError):
        resolve_skill(ref, agent_repo_tree=_agent_tree(), catalogue_tree={})


# ---- resolve_skill: catalogue -----------------------------------------


def test_resolve_named_skill_loads_from_catalogue():
    ref = SkillRef(name="pr-review", path=None, kind="catalogue")
    loaded = resolve_skill(ref, agent_repo_tree={}, catalogue_tree=_catalogue_tree())
    assert loaded.name == "PR Review"
    assert loaded.description == "reviews PRs"


def test_resolve_named_skill_absent_from_catalogue_raises():
    ref = SkillRef(name="does-not-exist", path=None, kind="catalogue")
    with pytest.raises(SkillResolutionError) as exc:
        resolve_skill(ref, agent_repo_tree={}, catalogue_tree=_catalogue_tree())
    assert "does-not-exist" in str(exc.value)
    assert "built-in catalogue" in str(exc.value)


def test_resolve_named_skill_does_not_fall_back_to_agent_repo():
    """A bare name resolves only against the catalogue, never the agent repo
    (the agent repo's matching folder is a *local* skill, declared as a table
    entry — a bare name is unambiguously a catalogue ref)."""
    ref = SkillRef(name="reviewer", path=None, kind="catalogue")
    # 'reviewer' exists in the agent tree but NOT the catalogue → miss.
    with pytest.raises(SkillResolutionError):
        resolve_skill(ref, agent_repo_tree=_agent_tree(), catalogue_tree=_catalogue_tree())


# ---- resolve_skill: org-repo guard ------------------------------------


def test_resolve_skill_rejects_org_repo_ref():
    """An org-repo ref must be resolved by the registration flow
    (resolve_org_repo_skill), not resolve_skill — passing one here is a
    caller bug surfaced loudly rather than mis-resolved against the catalogue."""
    ref = SkillRef(
        name="pr-review",
        kind="org_repo",
        repo_alias="acme",
        skill_subpath="dev-skills/pr-review",
    )
    with pytest.raises(SkillResolutionError):
        resolve_skill(ref, agent_repo_tree={}, catalogue_tree=_catalogue_tree())


# ---- resolve_org_repo_skill: spec 39d ---------------------------------


def _org_ref(subpath: str, *, ref: str = "v2") -> SkillRef:
    return SkillRef(
        name=subpath.rsplit("/", 1)[-1],
        kind="org_repo",
        repo_alias="acme",
        skill_subpath=subpath,
        ref=ref,
    )


def test_resolve_org_repo_skill_finds_under_skills_dir():
    """Preferred layout: ``skills/<subpath>/SKILL.md`` in the org repo."""
    tree = {"skills/dev-skills/pr-review/SKILL.md": _skill_md("PR Review", "org skill")}
    loaded = resolve_org_repo_skill(_org_ref("dev-skills/pr-review"), repo_tree=tree)
    assert loaded.name == "PR Review"
    assert loaded.description == "org skill"


def test_resolve_org_repo_skill_falls_back_to_bare_subpath():
    """Fallback layout: ``<subpath>/SKILL.md`` (repo not nested under skills/)."""
    tree = {"dev-skills/pr-review/SKILL.md": _skill_md("PR Review", "flat layout")}
    loaded = resolve_org_repo_skill(_org_ref("dev-skills/pr-review"), repo_tree=tree)
    assert loaded.name == "PR Review"
    assert loaded.description == "flat layout"


def test_resolve_org_repo_skill_missing_raises_resolution_error():
    tree = {"skills/other/SKILL.md": _skill_md("Other", "x")}
    with pytest.raises(SkillResolutionError) as exc:
        resolve_org_repo_skill(_org_ref("dev-skills/pr-review"), repo_tree=tree)
    assert "pr-review" in str(exc.value)


def test_resolve_org_repo_skill_rejects_non_org_repo_ref():
    ref = SkillRef(name="pr-review", path=None, kind="catalogue")
    with pytest.raises(SkillResolutionError):
        resolve_org_repo_skill(ref, repo_tree={})


# ---- fetch_org_repo_tree: public vs private routing -------------------


class _StubRepo:
    """Minimal OrgSkillRepo stand-in — fetch_org_repo_tree only reads
    ``source_connection`` + ``repo_full_name``."""

    def __init__(self, repo_full_name: str, source_connection=None):
        self.repo_full_name = repo_full_name
        self.source_connection = source_connection


def test_fetch_org_repo_tree_public_uses_anonymous_fetch(monkeypatch):
    from astrolift_agents.services import skill_resolver

    captured = {}

    def _fake_public(*, repo_full_name, ref):
        captured["repo"] = repo_full_name
        captured["ref"] = ref
        return {"skills/foo/SKILL.md": _skill_md("Foo", "x")}

    def _fail_private(*a, **k):  # must NOT be called for a public repo
        raise AssertionError("private fetch must not run for a public repo")

    monkeypatch.setattr("astrolift_scm.providers.repo_tree.fetch_public_repo_tree", _fake_public)
    monkeypatch.setattr("astrolift_scm.providers.repo_tree.fetch_repo_tree", _fail_private)

    tree = skill_resolver.fetch_org_repo_tree(_StubRepo("acme/dev-skills"), ref="v3")
    assert "skills/foo/SKILL.md" in tree
    assert captured == {"repo": "acme/dev-skills", "ref": "v3"}


def test_fetch_org_repo_tree_private_uses_connection_fetch(monkeypatch):
    from astrolift_agents.services import skill_resolver

    captured = {}
    sentinel_conn = object()

    def _fake_private(conn, *, repo_full_name, ref):
        captured["conn"] = conn
        captured["repo"] = repo_full_name
        captured["ref"] = ref
        return {"skills/bar/SKILL.md": _skill_md("Bar", "y")}

    def _fail_public(*a, **k):  # must NOT be called for a private repo
        raise AssertionError("public fetch must not run for a private repo")

    monkeypatch.setattr("astrolift_scm.providers.repo_tree.fetch_repo_tree", _fake_private)
    monkeypatch.setattr("astrolift_scm.providers.repo_tree.fetch_public_repo_tree", _fail_public)

    repo = _StubRepo("acme/private-skills", source_connection=sentinel_conn)
    tree = skill_resolver.fetch_org_repo_tree(repo, ref="main")
    assert "skills/bar/SKILL.md" in tree
    assert captured["conn"] is sentinel_conn
    assert captured["repo"] == "acme/private-skills"


# ---- catalogue settings + fetch ---------------------------------------


def test_catalogue_repo_and_ref_defaults(settings):
    # No settings override → spec 39 defaults.
    for attr in ("ASTROLIFT_SKILLS_CATALOGUE_REPO", "ASTROLIFT_SKILLS_CATALOGUE_REF"):
        if hasattr(settings, attr):
            delattr(settings, attr)
    assert catalogue_repo() == "calliopeai/astrolift-skills"
    assert catalogue_ref() == "main"


def test_catalogue_repo_and_ref_settings_override(settings):
    settings.ASTROLIFT_SKILLS_CATALOGUE_REPO = "acme/skills"
    settings.ASTROLIFT_SKILLS_CATALOGUE_REF = "v1.2.0"
    assert catalogue_repo() == "acme/skills"
    assert catalogue_ref() == "v1.2.0"


def _zipball(files: dict[str, str], *, top_dir: str = "astrolift-skills-main") -> bytes:
    """A GitHub /archive/<ref>.zip-shaped zipball (single top-level dir)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, body in files.items():
            zf.writestr(f"{top_dir}/{path}", body)
    return buf.getvalue()


class _Resp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")


def test_load_catalogue_tree_fetches_unauthenticated(monkeypatch):
    captured = {}

    def _get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _Resp(_zipball({"skills/pr-review/SKILL.md": _skill_md("PR Review", "x")}))

    monkeypatch.setattr("astrolift_scm.providers.repo_tree.requests.get", _get)

    tree = load_catalogue_tree()
    # Repo-relative paths (top-level dir stripped) — ready for load_skill.
    assert "skills/pr-review/SKILL.md" in tree
    # Anonymous public-archive URL, no Authorization header passed.
    assert captured["url"] == "https://github.com/calliopeai/astrolift-skills/archive/main.zip"
    assert not (captured["headers"] or {}).get("Authorization")


def test_load_catalogue_tree_network_failure_raises_catalogue_error(monkeypatch):
    import requests

    def _boom(url, **kwargs):
        raise requests.ConnectionError("no network")

    monkeypatch.setattr("astrolift_scm.providers.repo_tree.requests.get", _boom)
    with pytest.raises(CatalogueFetchError):
        load_catalogue_tree()


def test_load_catalogue_tree_http_error_raises_catalogue_error(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.repo_tree.requests.get",
        lambda url, **kwargs: _Resp(b"", status=404),
    )
    with pytest.raises(CatalogueFetchError):
        load_catalogue_tree()
