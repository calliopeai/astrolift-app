"""Unit tests for the brief-folder loader (spec 38, Phase 1).

``load_brief`` is pure over a ``{path: contents}`` repo-tree map, so these
tests use fixture file maps — no database or network.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.brief import BriefError, load_brief

_README = """# Triage Brief

Follow the [intake checklist](specs/intake.md) and the
[escalation policy](./specs/escalation.md).

Helper script: [runner](scripts/run.sh).

External docs live at [the wiki](https://example.com/wiki) and you can
email <ops@example.com>. See the [root license](../LICENSE) too.

Anchor-only link: [top](#triage-brief).
A link to a file that isn't in the repo: [ghost](specs/missing.md).
"""


def _brief_tree() -> dict[str, str]:
    return {
        "brief/README.md": _README,
        "brief/specs/intake.md": "# Intake\n",
        "brief/specs/escalation.md": "# Escalation\n",
        "brief/scripts/run.sh": "#!/bin/sh\n",
        # Not referenced by the README — must not be surfaced.
        "brief/specs/unused.md": "# Unused\n",
        # Outside the brief folder — an escaping ``../LICENSE`` must not leak.
        "LICENSE": "MIT",
        "README.md": "repo root readme",
    }


def test_load_brief_surfaces_in_folder_references_only():
    loaded = load_brief(_brief_tree(), readme_path="brief/README.md")
    assert loaded.readme_text.startswith("# Triage Brief")
    # Only the three sibling files that resolve inside the brief folder AND
    # exist in the tree are surfaced, sorted and de-duplicated.
    assert loaded.referenced_paths == (
        "brief/scripts/run.sh",
        "brief/specs/escalation.md",
        "brief/specs/intake.md",
    )


def test_load_brief_excludes_external_anchor_escaping_and_missing():
    loaded = load_brief(_brief_tree(), readme_path="brief/README.md")
    refs = set(loaded.referenced_paths)
    # External URL, mailto, escaping ../LICENSE, pure #anchor, and a
    # referenced-but-absent file are all dropped.
    assert "LICENSE" not in refs
    assert "brief/specs/missing.md" not in refs
    assert not any(r.startswith("http") for r in refs)
    # An unreferenced sibling that exists is also not surfaced.
    assert "brief/specs/unused.md" not in refs


def test_load_brief_dedupes_repeated_links():
    files = {
        "b/README.md": "[a](x.md) and again [a2](x.md) and [a3](./x.md)\n",
        "b/x.md": "x",
    }
    loaded = load_brief(files, readme_path="b/README.md")
    assert loaded.referenced_paths == ("b/x.md",)


def test_load_brief_root_readme_resolves_relative_paths():
    """A brief whose README sits at the repo root resolves its links against
    the root; non-escaping relative paths still qualify."""
    files = {
        "README.md": "see [doc](docs/guide.md)\n",
        "docs/guide.md": "# Guide\n",
    }
    loaded = load_brief(files, readme_path="README.md")
    assert loaded.referenced_paths == ("docs/guide.md",)


def test_load_brief_autolink_path_surfaced():
    files = {
        "b/README.md": "raw autolink <notes.md> here\n",
        "b/notes.md": "notes",
    }
    loaded = load_brief(files, readme_path="b/README.md")
    assert loaded.referenced_paths == ("b/notes.md",)


def test_load_brief_link_with_title_surfaced():
    files = {
        "b/README.md": '[doc](specs/x.md "A Title")\n',
        "b/specs/x.md": "x",
    }
    loaded = load_brief(files, readme_path="b/README.md")
    assert loaded.referenced_paths == ("b/specs/x.md",)


def test_load_brief_missing_readme_errors():
    with pytest.raises(BriefError) as exc:
        load_brief({"brief/specs/x.md": "x"}, readme_path="brief/README.md")
    assert exc.value.path == "brief/README.md"
    assert "not found" in str(exc.value)


def test_load_brief_non_text_readme_errors():
    with pytest.raises(BriefError) as exc:
        load_brief({"brief/README.md": b"binary"}, readme_path="brief/README.md")
    assert "text" in str(exc.value)


def test_load_brief_no_references_is_empty():
    files = {"b/README.md": "# Brief\n\nJust prose, no links.\n"}
    loaded = load_brief(files, readme_path="b/README.md")
    assert loaded.referenced_paths == ()
