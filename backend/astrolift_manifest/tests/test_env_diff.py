"""Tests for the manifest env diff (#1759 re-review).

``applyStagedManifest`` gates env changes on this diff and the editor's
confirm dialog lists its labels, so it must see every table a workload
reads env from, not only the app-wide ``[env]``: container env reaches
the pod and outranks ``[env]`` there.
"""

from __future__ import annotations

from astrolift_manifest.env_diff import changed_env_key_names, env_changes
from astrolift_manifest.parser import parse_raw

_BASE = """
name = "hello"

[env]
LOG_LEVEL = "info"

[[workloads]]
name = "web"
kind = "deployment"

[[workloads.containers]]
name = "web"
is_primary = true
"""


def test_an_unchanged_manifest_has_no_env_changes() -> None:
    assert env_changes(_BASE, _BASE) == []


def test_app_wide_env_add_change_and_remove_use_the_bare_key() -> None:
    after = _BASE.replace('LOG_LEVEL = "info"', 'LOG_LEVEL = "debug"\nNEW_KEY = "x"')
    assert changed_env_key_names(_BASE, after) == ["LOG_LEVEL", "NEW_KEY"]

    removed = _BASE.replace('LOG_LEVEL = "info"', "")
    changes = env_changes(_BASE, removed)
    assert [(c.label, c.app_wide, c.after) for c in changes] == [("LOG_LEVEL", True, None)]


def test_a_container_inline_env_table_is_an_env_change() -> None:
    """The reviewer's scenario: the key lands on the primary container, not in [env]."""
    after = _BASE.replace(
        "is_primary = true",
        'is_primary = true\nenv = { FOO = "attacker", DATABASE_URL = "postgres://evil" }',
    )
    changes = env_changes(_BASE, after)
    assert [c.label for c in changes] == [
        "workloads.web.containers.web.env.DATABASE_URL",
        "workloads.web.containers.web.env.FOO",
    ]
    assert not any(c.app_wide for c in changes)
    assert {c.after for c in changes} == {"attacker", "postgres://evil"}


def test_job_and_task_env_are_env_changes() -> None:
    before = (
        _BASE
        + """
[[jobs]]
name = "nightly"
schedule = "0 3 * * *"
env = { TOKEN = "a" }

[[tasks]]
name = "migrate"

[tasks.env]
DROP = "no"
"""
    )
    after = before.replace('TOKEN = "a"', 'TOKEN = "b"').replace('DROP = "no"', "")
    assert changed_env_key_names(before, after) == ["jobs.nightly.env.TOKEN", "tasks.migrate.env.DROP"]


def test_dotted_keys_and_nested_tables_in_env_are_seen() -> None:
    """A line-based reader misses both shapes; the TOML parse does not."""
    before = _BASE.replace('LOG_LEVEL = "info"', 'LOG_LEVEL = "info"\nNESTED = { inner = "a" }')
    after = before.replace('NESTED = { inner = "a" }', 'NESTED = { inner = "b" }')
    assert changed_env_key_names(before, after) == ["NESTED"]

    dotted = _BASE.replace('[env]\nLOG_LEVEL = "info"', 'env.LOG_LEVEL = "info"\nenv.EXTRA = "x"')
    assert changed_env_key_names(_BASE, dotted) == ["EXTRA"]


def test_a_type_change_with_the_same_text_is_a_change() -> None:
    """``1 == True`` in Python, but the rendered values differ."""
    one = _BASE.replace('LOG_LEVEL = "info"', "FLAG = 1")
    true = _BASE.replace('LOG_LEVEL = "info"', "FLAG = true")
    text = _BASE.replace('LOG_LEVEL = "info"', 'FLAG = "1"')
    assert changed_env_key_names(one, true) == ["FLAG"]
    assert changed_env_key_names(one, text) == ["FLAG"]


def test_repeated_container_names_cannot_hide_a_change() -> None:
    """Two containers sharing a name each keep their own identity, so a
    change on the first can't be masked by the unchanged second."""
    before = (
        _BASE
        + """
[[workloads.containers]]
name = "sidecar"
env = { KEY = "same" }

[[workloads.containers]]
name = "sidecar"
env = { KEY = "same" }
"""
    )
    after = before.replace('env = { KEY = "same" }', 'env = { KEY = "evil" }', 1)
    assert changed_env_key_names(before, after) == ["workloads.web.containers.sidecar.env.KEY"]


def test_dotted_names_cannot_make_two_containers_collide() -> None:
    """Joined into one string, both containers below would be
    ``workloads.a.containers.b.containers.c``, and the unchanged one,
    read last, would overwrite the new one and hide it."""
    existing = """
[[workloads]]
name = "a"
kind = "deployment"

[[workloads.containers]]
name = "b.containers.c"
env = { KEY = "orig" }
"""
    added = """
[[workloads]]
name = "a.containers.b"
kind = "deployment"

[[workloads.containers]]
name = "c"
env = { KEY = "evil" }
"""
    before = 'name = "hello"\n' + existing
    after = 'name = "hello"\n' + added + existing
    changes = env_changes(before, after)
    assert len(changes) == 1
    assert changes[0].after == "evil"


def test_an_unparseable_stored_manifest_counts_every_staged_key_as_added() -> None:
    after = _BASE.replace("is_primary = true", 'is_primary = true\nenv = { FOO = "x" }')
    assert changed_env_key_names("not [valid toml", after) == [
        "LOG_LEVEL",
        "workloads.web.containers.web.env.FOO",
    ]


def test_container_scopes_cover_exactly_what_the_parser_renders() -> None:
    """Every container env pair the parser produces, including the ones
    [[jobs]] / [[tasks]] desugar to, shows up here with the same value."""
    text = (
        _BASE.replace("is_primary = true", 'is_primary = true\nenv = { A = "1", B = 2 }')
        + """
[[jobs]]
name = "nightly"
schedule = "0 3 * * *"
env = { C = true }

[[tasks]]
name = "migrate"
env = { D = "4" }
"""
    )
    parsed = {
        value for workload in parse_raw(text).workloads for c in workload.containers for _, value in c.env
    }
    container_changes = [c for c in env_changes("", text) if not c.app_wide]
    assert {c.after for c in container_changes} == parsed
    assert len(container_changes) == 4
