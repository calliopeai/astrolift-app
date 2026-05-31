"""Tests for the pipeline context variable evaluator (#70)."""

from __future__ import annotations

import pytest

from astrolift_pipelines.context_eval import (
    ContextEvalError,
    PipelineContext,
    evaluate,
    evaluate_dict,
    extract_secret_name,
    is_secret_placeholder,
)


@pytest.fixture
def ctx():
    return PipelineContext(
        git={
            "sha": "abc123def456",
            "branch": "main",
            "tag": "",
            "repo": "acme/myapp",
            "actor": "octocat",
        },
        pipeline={
            "name": "ci",
            "run_id": "pipeline-run-42-7",
            "run_number": "7",
        },
        env={"NODE_ENV": "production", "APP_PORT": "8080"},
        secrets={"DATABASE_URL", "API_KEY"},
        job_outputs={"build": {"image_tag": "v1.2.3", "artifact_path": "/dist/app.tar.gz"}},
    )


# ---------------------------------------------------------------------------
# Basic substitution
# ---------------------------------------------------------------------------


def test_simple_ctx_git_substitution(ctx):
    assert evaluate("${ctx.git.sha}", ctx) == "abc123def456"
    assert evaluate("${ctx.git.branch}", ctx) == "main"
    assert evaluate("${ctx.git.repo}", ctx) == "acme/myapp"
    assert evaluate("${ctx.git.actor}", ctx) == "octocat"


def test_ctx_pipeline_substitution(ctx):
    assert evaluate("${ctx.pipeline.name}", ctx) == "ci"
    assert evaluate("${ctx.pipeline.run_number}", ctx) == "7"


def test_env_substitution(ctx):
    assert evaluate("${env.NODE_ENV}", ctx) == "production"
    assert evaluate("--port=${env.APP_PORT}", ctx) == "--port=8080"


def test_job_outputs_substitution(ctx):
    assert evaluate("${jobs.build.outputs.image_tag}", ctx) == "v1.2.3"
    assert evaluate("path=${jobs.build.outputs.artifact_path}", ctx) == "path=/dist/app.tar.gz"


def test_multiple_substitutions_in_one_string(ctx):
    result = evaluate("deploy ${ctx.git.repo}:${jobs.build.outputs.image_tag} to ${ctx.git.branch}", ctx)
    assert result == "deploy acme/myapp:v1.2.3 to main"


def test_no_substitution_leaves_string_unchanged(ctx):
    assert evaluate("echo hello world", ctx) == "echo hello world"
    assert evaluate("", ctx) == ""


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------


def test_secret_substituted_with_placeholder(ctx):
    result = evaluate("${secrets.DATABASE_URL}", ctx)
    assert is_secret_placeholder(result)
    assert extract_secret_name(result) == "DATABASE_URL"


def test_secret_placeholder_not_the_real_value(ctx):
    result = evaluate("${secrets.API_KEY}", ctx)
    assert "API_KEY" in result
    assert result != "actual-secret-value"


def test_is_secret_placeholder_false_for_real_string(ctx):
    assert not is_secret_placeholder("not-a-placeholder")
    assert not is_secret_placeholder("")


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_unknown_ctx_git_key_raises(ctx):
    with pytest.raises(ContextEvalError, match="Unknown ctx.git"):
        evaluate("${ctx.git.nonexistent}", ctx)


def test_unknown_env_key_raises(ctx):
    with pytest.raises(ContextEvalError, match="Unknown env"):
        evaluate("${env.MISSING_VAR}", ctx)


def test_unknown_job_raises(ctx):
    with pytest.raises(ContextEvalError, match="has no outputs"):
        evaluate("${jobs.nonexistent.outputs.foo}", ctx)


def test_unknown_job_output_key_raises(ctx):
    with pytest.raises(ContextEvalError, match="has no output"):
        evaluate("${jobs.build.outputs.nonexistent_key}", ctx)


def test_malformed_jobs_path_raises(ctx):
    with pytest.raises(ContextEvalError, match="jobs variable"):
        evaluate("${jobs.build.something}", ctx)


def test_unknown_namespace_raises(ctx):
    with pytest.raises(ContextEvalError, match="Unknown variable namespace"):
        evaluate("${unknown.something}", ctx)


def test_non_string_input_raises_type_error(ctx):
    with pytest.raises(TypeError):
        evaluate(123, ctx)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# evaluate_dict
# ---------------------------------------------------------------------------


def test_evaluate_dict_recurses(ctx):
    data = {
        "image": "ghcr.io/acme/app:${jobs.build.outputs.image_tag}",
        "env": {"SHA": "${ctx.git.sha}"},
        "replicas": 2,
        "tags": ["${ctx.git.branch}", "latest"],
    }
    result = evaluate_dict(data, ctx)
    assert result["image"] == "ghcr.io/acme/app:v1.2.3"
    assert result["env"]["SHA"] == "abc123def456"
    assert result["replicas"] == 2  # int untouched
    assert result["tags"] == ["main", "latest"]


def test_evaluate_dict_preserves_none(ctx):
    data = {"nullable": None, "flag": True}
    result = evaluate_dict(data, ctx)
    assert result == {"nullable": None, "flag": True}
