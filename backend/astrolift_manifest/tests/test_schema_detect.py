"""Unit tests for the app-vs-agent-config schema classifier (#1172).

``detect_toml_schema`` is a pure function over a TOML body — no DB, no
network — so each test is a tight body/classification pair. It must never
raise: garbage and unparseable input classify as ``"unknown"``.
"""

from __future__ import annotations

from astrolift_manifest.schema_detect import detect_toml_schema

# A realistic agent config-repo library: astrolift_version + [skills.*] +
# [tools.*] tables, no top-level name, no [[workloads]] (matches the schema
# ``astrolift_agents.services.skill_importer`` consumes).
_AGENT_CONFIG_TOML = """\
astrolift_version = 1

[skills.pr_review]
name = "PR Review"
description = "Reviews pull requests against the house style."
content = "You are a meticulous reviewer."
dependencies = ["gh"]

[tools.post_comment]
skill = "pr_review"
name = "Post comment"
adapter = "python_fn"
handler_ref = "tools.pr:post_comment"
"""

_APP_TOML = """\
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""


def test_agent_config_repo_is_detected():
    assert detect_toml_schema(_AGENT_CONFIG_TOML) == "agent_config"


def test_agent_config_with_only_skills_table():
    toml = 'astrolift_version = 1\n\n[skills.triage]\nname = "Triage"\ncontent = "..."\n'
    assert detect_toml_schema(toml) == "agent_config"


def test_agent_config_with_only_tools_table():
    toml = 'astrolift_version = 1\n\n[tools.grep]\nname = "grep"\nadapter = "shell"\n'
    assert detect_toml_schema(toml) == "agent_config"


def test_normal_app_manifest_is_app():
    assert detect_toml_schema(_APP_TOML) == "app"


def test_name_only_manifest_is_app():
    # A name and no workloads is still an (empty) app manifest — the backend
    # parser tolerates zero workloads.
    assert detect_toml_schema('name = "solo"\n') == "app"


def test_workloads_without_name_is_app():
    # A [[workloads]] block with no top-level name still reads as an app: it
    # has a deployable surface.
    toml = (
        "[[workloads]]\n"
        'name = "web"\n'
        'kind = "deployment"\n'
        "[[workloads.containers]]\n"
        'name = "app"\n'
        "port = 8080\n"
    )
    assert detect_toml_schema(toml) == "app"


def test_ambiguous_name_plus_skills_table_is_app():
    # Both a top-level name AND [skills.*] — the deployable surface wins the
    # tie, so this classifies as an app (never agent_config).
    toml = 'astrolift_version = 1\nname = "hybrid"\n\n[skills.x]\nname = "X"\ncontent = "c"\n'
    assert detect_toml_schema(toml) == "app"


def test_skills_as_array_is_not_agent_config():
    # The APP schema's ``skills`` is an *array* (spec 38/39 skill refs), not a
    # table. Without a name/workloads that's neither an app nor the agent
    # library schema — unknown, not a false agent_config.
    toml = 'astrolift_version = 1\nskills = ["pr-review", "triage"]\n'
    assert detect_toml_schema(toml) == "unknown"


def test_version_without_skills_or_tools_is_unknown():
    assert detect_toml_schema("astrolift_version = 1\n") == "unknown"


def test_empty_file_is_unknown():
    assert detect_toml_schema("") == "unknown"
    assert detect_toml_schema("   \n  \n") == "unknown"


def test_garbage_and_unparseable_are_unknown_not_raised():
    # Must never raise — a bad body is "unknown".
    assert detect_toml_schema("this is not = valid [ toml") == "unknown"
    assert detect_toml_schema("!!! \x00 garbage") == "unknown"
    assert detect_toml_schema("[unterminated\n") == "unknown"
