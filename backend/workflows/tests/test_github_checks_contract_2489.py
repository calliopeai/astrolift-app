"""The github_checks stage contract and its pass/fail rule, without IO (#2489)."""

import json
import tomllib

import pytest
import yaml

from astrolift_manifest.parser import ManifestError
from workflows.back_edges import LoopContractError, edge_matches, validate_back_edge, validate_loop_plan
from workflows.collections import CollectionContractError, validate_iteration
from workflows.github_checks import (
    GithubChecksContractError,
    evaluate_check_runs,
    outcome_message,
    pull_request_number,
    validate_github_checks,
)
from workflows.manifest import emit_workflow_manifest, parse_workflow_manifest
from workflows.models import WorkflowStage
from workflows.services import dsl_parser
from workflows.services.dsl_parser import parse_workflows_dsl, validate_workflow_dsl

CONFIG = {
    "repo": "ConflictHQ/fossilrepo",
    "sha_source": "pull_request_head",
    "pull_request_path": "build.pr",
    "checks": ["lint", "test", "conformance"],
}


def run(name, status="completed", conclusion="success", **extra):
    return {"name": name, "status": status, "conclusion": conclusion, **extra}


def test_valid_configuration_is_normalized_and_copied():
    checks = list(CONFIG["checks"])
    config = validate_github_checks({**CONFIG, "checks": checks})
    assert config == CONFIG
    checks.append("later")
    assert config["checks"] == ["lint", "test", "conformance"]


@pytest.mark.parametrize(
    "change,message",
    [
        ({"repo": None}, "repo"),
        ({"repo": "fossilrepo"}, "repo"),
        ({"repo": "ConflictHQ/fossilrepo/tree"}, "repo"),
        ({"repo": "-conflict/fossilrepo"}, "repo"),
        ({"repo": "ConflictHQ/.."}, "repo"),
        ({"repo": "https://github.com/ConflictHQ/fossilrepo"}, "repo"),
        ({"sha_source": "branch_head"}, "sha_source"),
        ({"sha_source": None}, "sha_source"),
        ({"pull_request_path": ""}, "pull_request_path"),
        ({"pull_request_path": "build..pr"}, "pull_request_path"),
        ({"pull_request_path": "build.pr[0]"}, "pull_request_path"),
        ({"pull_request_path": 7}, "pull_request_path"),
        ({"checks": []}, "checks"),
        ({"checks": "lint"}, "checks"),
        ({"checks": ["lint", "lint"]}, "checks"),
        ({"checks": [""]}, "checks"),
        ({"checks": [" lint"]}, "checks"),
        ({"checks": [7]}, "checks"),
        ({"checks": ["x" * 256]}, "checks"),
        ({"checks": [f"check-{n}" for n in range(21)]}, "checks"),
    ],
)
def test_invalid_configuration_is_refused(change, message):
    with pytest.raises(GithubChecksContractError, match=message):
        validate_github_checks({**CONFIG, **change})


@pytest.mark.parametrize(
    "value", [None, [], {}, {k: v for k, v in CONFIG.items() if k != "checks"}, {**CONFIG, "app": "x"}]
)
def test_configuration_needs_exactly_its_four_fields(value):
    with pytest.raises(
        GithubChecksContractError, match="exactly repo, sha_source, pull_request_path and checks"
    ):
        validate_github_checks(value)


def test_iteration_carries_the_gate_configuration_and_requires_it():
    assert validate_iteration(CONFIG, kind="github_checks") == CONFIG
    with pytest.raises(CollectionContractError, match="exactly repo"):
        validate_iteration({}, kind="github_checks")
    with pytest.raises(CollectionContractError, match="github_checks stages"):
        validate_iteration(CONFIG, kind="human_gate")


@pytest.mark.parametrize(
    "value,number",
    [
        (7, 7),
        ("7", 7),
        (" 456\n", 456),
        ("https://github.com/ConflictHQ/fossilrepo/pull/456", 456),
        ("https://github.com/conflicthq/FossilRepo/pull/456/files", 456),
        ("https://ghe.example.com/ConflictHQ/fossilrepo/pull/9?w=1", 9),
        ("http://github.com/ConflictHQ/fossilrepo/pull/9#discussion", 9),
        (2_147_483_647, 2_147_483_647),
    ],
)
def test_pull_request_is_read_from_the_preceding_output(value, number):
    assert pull_request_number(CONFIG, {"build": {"pr": value}}) == number


@pytest.mark.parametrize(
    "previous",
    [
        {},
        {"build": None},
        {"build": {}},
        {"build": {"pr": None}},
        {"build": {"pr": True}},
        {"build": {"pr": 0}},
        {"build": {"pr": -3}},
        {"build": {"pr": 7.0}},
        {"build": {"pr": 2_147_483_648}},
        {"build": {"pr": "07"}},
        {"build": {"pr": "7a"}},
        {"build": {"pr": "https://github.com/ConflictHQ/other/pull/7"}},
        {"build": {"pr": "https://github.com/ConflictHQ/fossilrepo/issues/7"}},
        {"build": {"pr": "https://api.github.com/repos/ConflictHQ/fossilrepo/pulls/7"}},
        {"build": {"pr": ["7"]}},
        "7",
    ],
)
def test_a_missing_or_foreign_pull_request_is_refused(previous):
    with pytest.raises(GithubChecksContractError):
        pull_request_number(CONFIG, previous)


def test_every_required_check_passes_on_the_conclusions_branch_protection_accepts():
    result = evaluate_check_runs(
        ["lint", "test", "conformance"],
        [
            run("lint", id=1, app={"slug": "github-actions"}, html_url="https://github.example/runs/1"),
            run("test", conclusion="neutral"),
            run("conformance", conclusion="skipped"),
            run("unrelated", conclusion="failure"),
        ],
    )
    assert result["outcome"] == "passed"
    assert [(check["name"], check["state"]) for check in result["checks"]] == [
        ("lint", "passed"),
        ("test", "passed"),
        ("conformance", "passed"),
    ]
    assert result["checks"][0]["runs"] == [
        {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "app": "github-actions",
            "url": "https://github.example/runs/1",
        }
    ]


@pytest.mark.parametrize(
    "conclusion", ["failure", "cancelled", "timed_out", "action_required", "stale", None]
)
def test_any_other_conclusion_fails_at_once_even_while_others_are_pending(conclusion):
    result = evaluate_check_runs(
        ["lint", "test"],
        [run("lint", status="in_progress", conclusion=None), run("test", conclusion=conclusion)],
    )
    assert result["outcome"] == "failed"
    assert [check["state"] for check in result["checks"]] == ["pending", "failed"]


def test_unfinished_or_missing_checks_keep_the_gate_pending():
    result = evaluate_check_runs(
        ["lint", "test", "conformance"],
        [run("lint"), run("test", status="queued", conclusion=None)],
    )
    assert result["outcome"] == "pending"
    assert [check["state"] for check in result["checks"]] == ["passed", "pending", "missing"]
    assert result["checks"][2]["runs"] == []


def test_every_run_of_a_required_name_counts():
    assert (
        evaluate_check_runs(["test"], [run("test"), run("test", conclusion="failure")])["outcome"] == "failed"
    )
    pending = evaluate_check_runs(["test"], [run("test"), run("test", status="in_progress", conclusion=None)])
    assert pending["outcome"] == "pending"
    many = [run("test") for _ in range(11)] + [run("test", conclusion="failure")]
    decided = evaluate_check_runs(["test"], many)
    assert decided["outcome"] == "failed"
    assert len(decided["checks"][0]["runs"]) == 10


def test_names_match_exactly_and_malformed_runs_are_ignored():
    result = evaluate_check_runs(
        ["test"],
        [
            "test",
            None,
            {"name": ["test"]},
            run("Test"),
            run("test ", conclusion="failure"),
            {"status": "completed"},
        ],
    )
    assert result["outcome"] == "pending"
    assert result["checks"] == [{"name": "test", "state": "missing", "runs": []}]


def test_outcome_messages_name_the_checks_that_decided_the_gate():
    checks = [
        {"name": "lint", "state": "passed"},
        {"name": "test", "state": "failed"},
        {"name": "conformance", "state": "missing"},
    ]
    assert outcome_message({"outcome": "failed", "checks": checks}) == "required GitHub checks failed: test"
    assert outcome_message(
        {"outcome": "timed_out", "checks": checks, "error": "GitHub rate limit reached"}
    ) == (
        "required GitHub checks did not pass before the stage timeout: test (failed), conformance (missing); "
        "last error: GitHub rate limit reached"
    )
    assert outcome_message({"outcome": "error", "checks": [], "error": "no connection"}) == "no connection"


def test_an_output_equals_return_edge_can_route_a_failed_gate():
    edge = validate_back_edge(
        {
            "to": "build",
            "when": "output_equals",
            "path": "github_checks.outcome",
            "value": "failed",
            "max_rounds": 2,
        },
        kind="github_checks",
    )
    assert edge_matches(edge, {"github_checks": {"outcome": "failed"}})
    assert not edge_matches(edge, {"github_checks": {"outcome": "passed"}})
    for when in ("gate_rejected", "stage_failed", "always"):
        with pytest.raises(LoopContractError):
            validate_back_edge({"to": "build", "when": when, "max_rounds": 2}, kind="github_checks")


def test_plan_validation_accepts_a_gate_after_its_producer():
    validate_loop_plan(
        [
            {"order": 0, "kind": "checkpoint", "output_key": "build"},
            {"order": 1, "kind": "github_checks", "output_key": "ci", "iteration": CONFIG},
        ],
        pattern_kind="chained",
    )
    with pytest.raises(LoopContractError, match="exactly repo"):
        validate_loop_plan(
            [{"order": 0, "kind": "github_checks", "output_key": "ci", "iteration": {}}],
            pattern_kind="chained",
        )


MANIFEST = """
[workflow]
slug = "fossil-story"
name = "Fossil story"
pattern = "chained"

[[stage]]
kind = "checkpoint"
output_key = "build"

[[stage]]
kind = "github_checks"
output_key = "ci"
timeout = 3600
iteration = { repo = "ConflictHQ/fossilrepo", sha_source = "pull_request_head", pull_request_path = "build.pr", checks = ["lint", "test", "conformance"] }
back_edge = { to = "build", when = "output_equals", path = "github_checks.outcome", value = "failed", max_rounds = 3 }
"""


def test_toml_manifest_round_trips_the_gate():
    parsed = parse_workflow_manifest(MANIFEST)
    gate = parsed.stages[1]
    assert (gate.kind, gate.timeout, gate.iteration) == ("github_checks", 3600, CONFIG)
    emitted = emit_workflow_manifest(parsed)
    assert json.loads(tomllib.loads(emitted)["stage"][1]["iteration_json"]) == CONFIG
    assert parse_workflow_manifest(emitted) == parsed


def test_toml_manifest_refuses_an_incomplete_gate():
    with pytest.raises(ManifestError) as caught:
        parse_workflow_manifest(MANIFEST.replace(', checks = ["lint", "test", "conformance"]', ""))
    assert caught.value.path == "stage[1].iteration"


def test_repository_yaml_carries_the_gate():
    document = yaml.safe_dump(
        {
            "workflows": [
                {
                    "slug": "fossil-story",
                    "name": "Fossil story",
                    "pattern_kind": "chained",
                    "stages": [
                        {"kind": "checkpoint", "output_key": "build"},
                        {
                            "kind": "github_checks",
                            "output_key": "ci",
                            "timeout_seconds": 3600,
                            "iteration": CONFIG,
                        },
                    ],
                }
            ]
        }
    )
    [definition] = parse_workflows_dsl(document)
    assert definition["stages"][1]["kind"] == "github_checks"
    assert definition["stages"][1]["iteration"] == CONFIG
    assert validate_workflow_dsl(definition) == []


def test_repository_yaml_stage_kinds_match_the_model():
    assert dsl_parser._VALID_STAGE_KINDS == set(WorkflowStage.StageKind.values)
