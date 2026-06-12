"""Tests for in-repo YAML DSL parser and WorkflowDefinition sync (#866)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from workflows.services.dsl_parser import (
    DslParseError,
    parse_workflows_dsl,
    validate_workflow_dsl,
)

# ---------------------------------------------------------------------------
# parse_workflows_dsl — structural parsing
# ---------------------------------------------------------------------------

MINIMAL_YAML = """\
workflows:
  - slug: "code-review"
    name: "Code Review"
    pattern_kind: "chained"
    stages:
      - kind: "agent_dispatch"
        skill_refs: ["code-reviewer"]
        timeout_seconds: 300
        on_failure: "retry"
      - kind: "human_gate"
        timeout_seconds: 3600
        on_failure: "fail"
"""

FULL_YAML = """\
workflows:
  - slug: "fan-out-pipeline"
    name: "Fan-out Pipeline"
    pattern_kind: "fan_out"
    model_label: "workflows.workflowdefinition"
    is_enabled: false
    states:
      - name: "pending"
        label: "Pending"
        is_initial: true
        is_final: false
      - name: "done"
        label: "Done"
        is_initial: false
        is_final: true
    transitions:
      - from_state: "pending"
        to_state: "done"
        label: "Complete"
    stages:
      - kind: "agent_dispatch"
        skill_refs: ["analyst", "reporter"]
        fan_out_count: 4
        timeout_seconds: 600
        on_failure: "escalate"
  - slug: "simple-advisor"
    name: "Simple Advisor"
    pattern_kind: "advisor"
    stages:
      - kind: "checkpoint"
        timeout_seconds: 120
        on_failure: "skip"
"""


def test_parse_minimal_yaml_produces_correct_structure():
    """parse_workflows_dsl returns expected dict shape for a minimal workflow."""
    results = parse_workflows_dsl(MINIMAL_YAML)

    assert len(results) == 1
    wf = results[0]
    assert wf["slug"] == "code-review"
    assert wf["name"] == "Code Review"
    assert wf["pattern_kind"] == "chained"
    assert wf["model_label"] == ""
    assert wf["is_enabled"] is True

    stages = wf["stages"]
    assert len(stages) == 2

    s0 = stages[0]
    assert s0["order"] == 0
    assert s0["kind"] == "agent_dispatch"
    assert s0["skill_refs"] == ["code-reviewer"]
    assert s0["timeout_seconds"] == 300
    assert s0["on_failure"] == "retry"
    assert s0["fan_out_count"] is None

    s1 = stages[1]
    assert s1["order"] == 1
    assert s1["kind"] == "human_gate"
    assert s1["timeout_seconds"] == 3600
    assert s1["on_failure"] == "fail"


def test_parse_full_yaml_produces_all_fields():
    """parse_workflows_dsl reads optional fields when present."""
    results = parse_workflows_dsl(FULL_YAML)

    assert len(results) == 2

    wf = results[0]
    assert wf["slug"] == "fan-out-pipeline"
    assert wf["pattern_kind"] == "fan_out"
    assert wf["model_label"] == "workflows.workflowdefinition"
    assert wf["is_enabled"] is False

    stage = wf["stages"][0]
    assert stage["skill_refs"] == ["analyst", "reporter"]
    assert stage["fan_out_count"] == 4
    assert stage["on_failure"] == "escalate"

    # Second workflow in the list
    wf2 = results[1]
    assert wf2["slug"] == "simple-advisor"
    assert wf2["pattern_kind"] == "advisor"
    assert wf2["stages"][0]["kind"] == "checkpoint"


def test_parse_inserts_default_states_when_omitted():
    """When states/transitions are omitted, defaults are injected."""
    results = parse_workflows_dsl(MINIMAL_YAML)
    wf = results[0]
    assert any(s.get("is_initial") for s in wf["states"])
    assert any(s.get("is_final") for s in wf["states"])
    assert len(wf["transitions"]) >= 1


def test_parse_raises_on_invalid_yaml_syntax():
    """DslParseError is raised when the YAML is syntactically invalid."""
    bad_yaml = "workflows:\n  - slug: [unclosed"
    with pytest.raises(DslParseError, match="YAML syntax error"):
        parse_workflows_dsl(bad_yaml)


def test_parse_raises_when_workflows_key_missing():
    """DslParseError raised when the 'workflows' key is absent."""
    with pytest.raises(DslParseError, match="'workflows' list"):
        parse_workflows_dsl("name: test\n")


def test_parse_raises_when_stages_missing():
    """DslParseError raised when a workflow entry has no 'stages' key."""
    yaml_no_stages = """\
workflows:
  - slug: "no-stages"
    name: "No Stages"
"""
    with pytest.raises(DslParseError, match="stages"):
        parse_workflows_dsl(yaml_no_stages)


def test_parse_raises_when_slug_missing():
    """DslParseError raised when 'slug' is absent from a workflow entry."""
    yaml_no_slug = """\
workflows:
  - name: "No Slug"
    stages:
      - kind: "checkpoint"
"""
    with pytest.raises(DslParseError, match="slug"):
        parse_workflows_dsl(yaml_no_slug)


# ---------------------------------------------------------------------------
# validate_workflow_dsl — semantic validation
# ---------------------------------------------------------------------------


def _parsed_valid():
    return parse_workflows_dsl(MINIMAL_YAML)[0]


def test_validate_returns_empty_list_for_valid_definition():
    """validate_workflow_dsl returns no errors for a well-formed definition."""
    defn = _parsed_valid()
    assert validate_workflow_dsl(defn) == []


def test_validate_returns_error_for_invalid_pattern_kind():
    """validate_workflow_dsl reports an error for an unknown pattern_kind."""
    defn = _parsed_valid()
    defn["pattern_kind"] = "nonsense"
    errors = validate_workflow_dsl(defn)
    assert any("pattern_kind" in e for e in errors)


def test_validate_returns_error_for_invalid_stage_kind():
    """validate_workflow_dsl reports an error for an unknown stage kind."""
    defn = _parsed_valid()
    defn["stages"][0]["kind"] = "invalid_kind"
    errors = validate_workflow_dsl(defn)
    assert any("kind" in e for e in errors)


def test_validate_returns_error_for_invalid_on_failure():
    """validate_workflow_dsl reports an error for an unknown on_failure value."""
    defn = _parsed_valid()
    defn["stages"][0]["on_failure"] = "explode"
    errors = validate_workflow_dsl(defn)
    assert any("on_failure" in e for e in errors)


def test_validate_returns_error_when_no_stages():
    """validate_workflow_dsl reports an error when the stage list is empty."""
    defn = _parsed_valid()
    defn["stages"] = []
    errors = validate_workflow_dsl(defn)
    assert any("stage" in e.lower() for e in errors)


def test_validate_returns_error_for_missing_slug():
    """validate_workflow_dsl reports an error when slug is empty."""
    defn = _parsed_valid()
    defn["slug"] = ""
    errors = validate_workflow_dsl(defn)
    assert any("slug" in e for e in errors)


# ---------------------------------------------------------------------------
# sync_workflows_from_repo — DB interaction (requires django_db)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sync_creates_definition_and_stages_on_first_run():
    """sync_workflows_from_repo creates WorkflowDefinition + stages on first call."""
    from workflows.models import WorkflowDefinition, WorkflowStage
    from workflows.services.workflow_sync import sync_workflows_from_repo

    app = MagicMock()
    app.source_repo = "acme/my-app"
    connection = MagicMock()

    with patch(
        "astrolift_scm.providers.fetch_file", return_value=MINIMAL_YAML
    ):
        result = sync_workflows_from_repo(app, connection, ref="abc123")

    assert result["created"] == 1
    assert result["updated"] == 0
    assert result["errors"] == []

    defn = WorkflowDefinition.objects.get(slug="code-review")
    assert defn.name == "Code Review"
    assert defn.pattern_kind == "chained"

    stages = list(WorkflowStage.objects.filter(definition=defn).order_by("order"))
    assert len(stages) == 2
    assert stages[0].kind == "agent_dispatch"
    assert stages[0].skill_refs == ["code-reviewer"]
    assert stages[0].timeout_seconds == 300
    assert stages[1].kind == "human_gate"


@pytest.mark.django_db
def test_sync_updates_definition_and_replaces_stages_on_second_run():
    """sync_workflows_from_repo updates definition and replaces stages idempotently."""
    from workflows.models import WorkflowDefinition, WorkflowStage
    from workflows.services.workflow_sync import sync_workflows_from_repo

    app = MagicMock()
    app.source_repo = "acme/my-app"
    connection = MagicMock()

    with patch(
        "workflows.services.workflow_sync.fetch_file", return_value=MINIMAL_YAML
    ):
        sync_workflows_from_repo(app, connection, ref="abc123")

    # Second run with an updated YAML — one stage removed, name changed.
    updated_yaml = """\
workflows:
  - slug: "code-review"
    name: "Code Review v2"
    pattern_kind: "chained"
    stages:
      - kind: "human_gate"
        timeout_seconds: 7200
        on_failure: "fail"
"""

    with patch(
        "astrolift_scm.providers.fetch_file", return_value=updated_yaml
    ):
        result = sync_workflows_from_repo(app, connection, ref="def456")

    assert result["created"] == 0
    assert result["updated"] == 1
    assert result["errors"] == []

    defn = WorkflowDefinition.objects.get(slug="code-review")
    assert defn.name == "Code Review v2"

    stages = list(WorkflowStage.objects.filter(definition=defn).order_by("order"))
    assert len(stages) == 1
    assert stages[0].kind == "human_gate"
    assert stages[0].timeout_seconds == 7200


@pytest.mark.django_db
def test_sync_returns_empty_result_when_file_not_found():
    """sync_workflows_from_repo returns zeros when the DSL file is absent."""
    from workflows.services.workflow_sync import sync_workflows_from_repo

    app = MagicMock()
    app.source_repo = "acme/no-workflows-app"
    connection = MagicMock()

    with patch("astrolift_scm.providers.fetch_file", return_value=None):
        result = sync_workflows_from_repo(app, connection, ref="abc123")

    assert result == {"created": 0, "updated": 0, "errors": []}


@pytest.mark.django_db
def test_sync_records_error_without_raising_on_invalid_dsl():
    """sync_workflows_from_repo records the parse error but does not raise."""
    from workflows.services.workflow_sync import sync_workflows_from_repo

    app = MagicMock()
    app.source_repo = "acme/bad-dsl-app"
    connection = MagicMock()

    bad_yaml = "workflows:\n  - not_a_workflow: true\n"

    with patch("astrolift_scm.providers.fetch_file", return_value=bad_yaml):
        result = sync_workflows_from_repo(app, connection, ref="abc123")

    assert result["created"] == 0
    assert result["updated"] == 0
    assert len(result["errors"]) > 0
