"""Unit tests for the agentskills.io skill-folder loader (spec 38, Phase 1).

``load_skill`` is pure over a ``{path: contents}`` repo-tree map (the same
shape ``scan_agent_manifests`` / the zipball unpacker produce), so these
tests need no database or network — just fixture file maps.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.skills import SkillError, load_skill

_SKILL_MD = """---
name: code-reviewer
description: Reviews a diff and flags risky changes.
license: Apache-2.0
allowed-tools:
  - read
  - grep
---
# Code Reviewer

Read the diff and call out anything risky.

## Steps
1. Read the patch.
2. Flag risky changes.
"""


def _skill_tree() -> dict[str, str]:
    return {
        "skills/reviewer/SKILL.md": _SKILL_MD,
        "skills/reviewer/scripts/run.py": "print('hi')\n",
        "skills/reviewer/scripts/lib/helper.py": "X = 1\n",
        "skills/reviewer/references/style-guide.md": "# Style\n",
        "skills/reviewer/assets/template.txt": "hello\n",
        # A sibling skill that must not bleed into this one.
        "skills/other/SKILL.md": "---\nname: other\ndescription: nope\n---\nbody\n",
        # Repo-root noise.
        "README.md": "repo docs",
    }


def test_load_skill_parses_frontmatter_body_and_bundles():
    loaded = load_skill(_skill_tree(), folder="skills/reviewer")
    assert loaded.name == "code-reviewer"
    assert loaded.description == "Reviews a diff and flags risky changes."
    # Body is the markdown after the frontmatter, stripped.
    assert loaded.instructions.startswith("# Code Reviewer")
    assert "Flag risky changes." in loaded.instructions
    # Frontmatter fence is not part of the instructions.
    assert "---" not in loaded.instructions.splitlines()[0]
    # Bundles enumerated at any depth, sorted, scoped to this folder.
    assert loaded.scripts == (
        "skills/reviewer/scripts/lib/helper.py",
        "skills/reviewer/scripts/run.py",
    )
    assert loaded.references == ("skills/reviewer/references/style-guide.md",)
    assert loaded.assets == ("skills/reviewer/assets/template.txt",)
    # The sibling skill never leaks in.
    assert all("skills/other" not in p for p in loaded.scripts + loaded.references + loaded.assets)


def test_load_skill_passes_through_optional_frontmatter():
    """Optional keys beyond name/description are not enumerated by the loader
    — they pass through on ``frontmatter`` for Phase 2 to consume."""
    loaded = load_skill(_skill_tree(), folder="skills/reviewer")
    assert loaded.frontmatter["license"] == "Apache-2.0"
    assert loaded.frontmatter["allowed-tools"] == ["read", "grep"]


def test_load_skill_tolerates_trailing_slash_and_no_bundles():
    files = {"a/b/SKILL.md": "---\nname: x\ndescription: y\n---\nbody text\n"}
    loaded = load_skill(files, folder="a/b/")
    assert loaded.name == "x"
    assert loaded.instructions == "body text"
    assert loaded.scripts == () and loaded.references == () and loaded.assets == ()


def test_load_skill_missing_skill_md_errors():
    files = {"skills/reviewer/scripts/run.py": "x"}
    with pytest.raises(SkillError) as exc:
        load_skill(files, folder="skills/reviewer")
    assert exc.value.path == "skills/reviewer"
    assert "SKILL.md" in str(exc.value)


def test_load_skill_missing_name_errors():
    files = {"s/SKILL.md": "---\ndescription: only a description\n---\nbody\n"}
    with pytest.raises(SkillError) as exc:
        load_skill(files, folder="s")
    assert exc.value.path == "s/SKILL.md#name"


def test_load_skill_missing_description_errors():
    files = {"s/SKILL.md": "---\nname: only-a-name\n---\nbody\n"}
    with pytest.raises(SkillError) as exc:
        load_skill(files, folder="s")
    assert exc.value.path == "s/SKILL.md#description"


def test_load_skill_no_frontmatter_errors():
    files = {"s/SKILL.md": "# Just markdown, no frontmatter\n"}
    with pytest.raises(SkillError) as exc:
        load_skill(files, folder="s")
    assert "frontmatter" in str(exc.value)


def test_load_skill_unclosed_frontmatter_errors():
    files = {"s/SKILL.md": "---\nname: x\ndescription: y\n"}
    with pytest.raises(SkillError) as exc:
        load_skill(files, folder="s")
    assert "not closed" in str(exc.value)


def test_load_skill_non_mapping_frontmatter_errors():
    files = {"s/SKILL.md": "---\n- just\n- a\n- list\n---\nbody\n"}
    with pytest.raises(SkillError) as exc:
        load_skill(files, folder="s")
    assert "mapping" in str(exc.value)


def test_load_skill_invalid_yaml_frontmatter_errors():
    files = {"s/SKILL.md": "---\nname: x\n description: bad: indent: here\n---\nbody\n"}
    with pytest.raises(SkillError) as exc:
        load_skill(files, folder="s")
    assert "YAML" in str(exc.value)


def test_load_skill_non_text_contents_errors():
    files = {"s/SKILL.md": b"binary"}
    with pytest.raises(SkillError) as exc:
        load_skill(files, folder="s")
    assert "text" in str(exc.value)


def test_load_skill_blank_required_value_errors():
    """A present-but-blank ``name`` is as bad as a missing one."""
    files = {"s/SKILL.md": '---\nname: "   "\ndescription: y\n---\nbody\n'}
    with pytest.raises(SkillError) as exc:
        load_skill(files, folder="s")
    assert exc.value.path == "s/SKILL.md#name"
