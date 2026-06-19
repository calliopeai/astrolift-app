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
    ref = SkillRef(name="reviewer", path="skills/reviewer", is_local=True)
    loaded = resolve_skill(ref, agent_repo_tree=_agent_tree(), catalogue_tree={})
    assert loaded.name == "Reviewer"
    assert loaded.description == "reviews code"
    assert loaded.instructions == "do the thing"


def test_resolve_local_skill_missing_path_raises():
    ref = SkillRef(name="ghost", path="skills/ghost", is_local=True)
    with pytest.raises(SkillError):  # subclass of SkillResolutionError's base ManifestError
        resolve_skill(ref, agent_repo_tree=_agent_tree(), catalogue_tree={})


def test_resolve_local_skill_without_path_raises_resolution_error():
    # Defensive branch: a local ref with no path can't be located.
    ref = SkillRef(name="reviewer", path=None, is_local=True)
    with pytest.raises(SkillResolutionError):
        resolve_skill(ref, agent_repo_tree=_agent_tree(), catalogue_tree={})


# ---- resolve_skill: named (catalogue) ---------------------------------


def test_resolve_named_skill_loads_from_catalogue():
    ref = SkillRef(name="pr-review", path=None, is_local=False)
    loaded = resolve_skill(ref, agent_repo_tree={}, catalogue_tree=_catalogue_tree())
    assert loaded.name == "PR Review"
    assert loaded.description == "reviews PRs"


def test_resolve_named_skill_absent_from_catalogue_raises():
    ref = SkillRef(name="does-not-exist", path=None, is_local=False)
    with pytest.raises(SkillResolutionError) as exc:
        resolve_skill(ref, agent_repo_tree={}, catalogue_tree=_catalogue_tree())
    assert "does-not-exist" in str(exc.value)
    assert "built-in catalogue" in str(exc.value)


def test_resolve_named_skill_does_not_fall_back_to_agent_repo():
    """A bare name resolves only against the catalogue, never the agent repo
    (the agent repo's matching folder is a *local* skill, declared as a table
    entry — a bare name is unambiguously a catalogue/org ref)."""
    ref = SkillRef(name="reviewer", path=None, is_local=False)
    # 'reviewer' exists in the agent tree but NOT the catalogue → miss.
    with pytest.raises(SkillResolutionError):
        resolve_skill(ref, agent_repo_tree=_agent_tree(), catalogue_tree=_catalogue_tree())


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
