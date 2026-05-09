"""Tests for CI deploy endpoint policy (#17, spec 07 §9)."""

from __future__ import annotations

import pytest

from astrolift_lifecycle.ci_deploy import (
    RATE_LIMIT_PER_MINUTE,
    CiDeployError,
    CiDeployRequest,
    TriggerKind,
    build_handle,
    parse_request,
    polling_url_for,
    rate_limit_for,
    validate_branch,
    validate_commit_sha,
    validate_environment,
    validate_image_tag,
    validate_image_tags,
    validate_trigger_kind,
    workflow_id_for,
)

# ---- image tag validation ------------------------------------------


def test_image_tag_hex_digest():
    assert validate_image_tag(value="abc1234567890") == "abc1234567890"


def test_image_tag_full_sha256():
    """Full 64-char SHA-256."""
    digest = "a" * 64
    assert validate_image_tag(value=digest) == digest


def test_image_tag_oci_tag_name():
    assert validate_image_tag(value="v1.2.3") == "v1.2.3"
    assert validate_image_tag(value="release-2024-05") == "release-2024-05"


def test_image_tag_rejects_empty():
    with pytest.raises(CiDeployError):
        validate_image_tag(value="")


def test_image_tag_rejects_invalid_chars():
    """Slash isn't valid in OCI tag (would conflict with repo path)."""
    with pytest.raises(CiDeployError):
        validate_image_tag(value="foo/bar")


# ---- image tags map -----------------------------------------------


def test_image_tags_filters_invalid_workloads():
    """Tag for workload not in manifest = operator typo defense."""
    with pytest.raises(CiDeployError, match="not declared"):
        validate_image_tags(
            workload_tags={"web": "abc1234"},
            declared_workloads=("api",),
        )


def test_image_tags_accepts_subset():
    """Partial deploys are OK at the validation layer; the
    workflow decides what to do with absent workloads."""
    out = validate_image_tags(
        workload_tags={"web": "abc1234"},
        declared_workloads=("web", "worker"),
    )
    assert out == {"web": "abc1234"}


def test_image_tags_rejects_empty_map():
    with pytest.raises(CiDeployError):
        validate_image_tags(
            workload_tags={}, declared_workloads=("web",),
        )


def test_image_tags_rejects_empty_workload_slug():
    with pytest.raises(CiDeployError, match="slug"):
        validate_image_tags(
            workload_tags={"": "abc1234"},
            declared_workloads=("web",),
        )


# ---- branch + commit -----------------------------------------------


def test_branch_basic():
    assert validate_branch(value="main") == "main"
    assert validate_branch(value="feature/foo-bar") == "feature/foo-bar"


def test_branch_rejects_empty():
    with pytest.raises(CiDeployError):
        validate_branch(value="")


def test_branch_rejects_spaces():
    with pytest.raises(CiDeployError):
        validate_branch(value="feature with spaces")


def test_commit_sha_full_sha1():
    sha = "a" * 40
    assert validate_commit_sha(value=sha) == sha


def test_commit_sha_full_sha256():
    sha = "b" * 64
    assert validate_commit_sha(value=sha) == sha


def test_commit_sha_short_rejected():
    """Short SHAs collide; CI provides full one."""
    with pytest.raises(CiDeployError, match="too short"):
        validate_commit_sha(value="abc1234")


def test_commit_sha_non_hex_rejected():
    with pytest.raises(CiDeployError):
        validate_commit_sha(value="x" * 40)


# ---- environment ---------------------------------------------------


def test_environment_known():
    assert validate_environment(
        name="prod", registered_envs=("prod", "staging"),
    ) == "prod"


def test_environment_typo_rejected():
    """'preod' instead of 'prod' would silently deploy nothing
    if not validated."""
    with pytest.raises(CiDeployError, match="not registered"):
        validate_environment(
            name="preod", registered_envs=("prod", "staging"),
        )


def test_environment_empty_rejected():
    with pytest.raises(CiDeployError):
        validate_environment(name="", registered_envs=("prod",))


# ---- trigger kind --------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("ci", TriggerKind.CI),
    ("manual_cli", TriggerKind.MANUAL_CLI),
    ("webhook_scm", TriggerKind.WEBHOOK_SCM),
    ("scheduled", TriggerKind.SCHEDULED),
])
def test_trigger_kind_known(raw, expected):
    assert validate_trigger_kind(value=raw) == expected


def test_trigger_kind_unknown_rejected():
    with pytest.raises(CiDeployError, match="vocabulary"):
        validate_trigger_kind(value="cron")


# ---- parse_request -------------------------------------------------


def _body(**overrides):
    base = {
        "image_tags": {"web": "abc1234567890"},
        "commit_sha": "a" * 40,
        "branch": "main",
        "environment": "prod",
        "trigger_kind": "ci",
    }
    base.update(overrides)
    return base


def test_parse_request_happy_path():
    req = parse_request(
        app_slug="acme",
        raw_body=_body(),
        declared_workloads=("web",),
        registered_envs=("prod",),
    )
    assert req.app_slug == "acme"
    assert req.environment == "prod"
    assert req.trigger_kind == TriggerKind.CI


def test_parse_request_with_idempotency_key():
    req = parse_request(
        app_slug="acme",
        raw_body=_body(idempotency_key="ci-build-12345"),
        declared_workloads=("web",),
        registered_envs=("prod",),
    )
    assert req.idempotency_key == "ci-build-12345"


def test_parse_request_default_trigger_kind_is_ci():
    body = _body()
    del body["trigger_kind"]
    req = parse_request(
        app_slug="acme", raw_body=body,
        declared_workloads=("web",), registered_envs=("prod",),
    )
    assert req.trigger_kind == TriggerKind.CI


def test_parse_request_rejects_non_dict():
    with pytest.raises(CiDeployError, match="JSON object"):
        parse_request(
            app_slug="acme", raw_body="not a dict",  # type: ignore[arg-type]
            declared_workloads=("web",), registered_envs=("prod",),
        )


def test_parse_request_rejects_empty_app_slug():
    with pytest.raises(CiDeployError):
        parse_request(
            app_slug="", raw_body=_body(),
            declared_workloads=("web",), registered_envs=("prod",),
        )


# ---- workflow id determinism ---------------------------------------


def _req(**overrides) -> CiDeployRequest:
    base = dict(
        app_slug="acme",
        image_tags={"web": "abc1234567890"},
        commit_sha="a" * 40,
        branch="main",
        environment="prod",
        trigger_kind=TriggerKind.CI,
    )
    base.update(overrides)
    return CiDeployRequest(**base)


def test_workflow_id_deterministic():
    """Same request inputs → same workflow ID. Re-delivery
    lands on same Temporal workflow (de-dupes)."""
    a = workflow_id_for(request=_req())
    b = workflow_id_for(request=_req())
    assert a == b


def test_workflow_id_different_for_different_commit():
    a = workflow_id_for(request=_req(commit_sha="a" * 40))
    b = workflow_id_for(request=_req(commit_sha="b" * 40))
    assert a != b


def test_workflow_id_different_for_different_image_tags():
    """Same commit, different image tag → different workflow.
    Catches the retag-with-same-commit pattern."""
    a = workflow_id_for(request=_req(image_tags={"web": "abc1234567890"}))
    b = workflow_id_for(request=_req(image_tags={"web": "def1234567890"}))
    assert a != b


def test_workflow_id_different_for_different_env():
    a = workflow_id_for(request=_req(environment="prod"))
    b = workflow_id_for(request=_req(environment="staging"))
    # Note: this would fail the env validator if envs differ;
    # here we're just testing the ID computes differently
    assert a != b


def test_workflow_id_idempotency_key_distinct():
    """Operator-supplied idempotency_key changes the ID even
    when other fields match — explicit dedupe boundary."""
    a = workflow_id_for(request=_req(idempotency_key="key-1"))
    b = workflow_id_for(request=_req(idempotency_key="key-2"))
    assert a != b


def test_workflow_id_includes_app_slug():
    """Different apps with same commit → distinct IDs."""
    a = workflow_id_for(request=_req(app_slug="acme"))
    b = workflow_id_for(request=_req(app_slug="globex"))
    assert a != b
    assert "acme" in a
    assert "globex" in b


def test_workflow_id_image_tag_order_independent():
    """{web, worker} vs {worker, web} → same ID. Tags sorted
    internally."""
    a = workflow_id_for(request=_req(
        image_tags={"web": "a" * 12, "worker": "b" * 12},
    ))
    b = workflow_id_for(request=_req(
        image_tags={"worker": "b" * 12, "web": "a" * 12},
    ))
    assert a == b


# ---- handle building ----------------------------------------------


def test_polling_url_format():
    url = polling_url_for(workflow_id="deploy-acme-prod-abc")
    assert url == "/api/cli/v1/workflow_runs/deploy-acme-prod-abc"


def test_polling_url_custom_base():
    url = polling_url_for(
        workflow_id="x", api_base="/api/v2",
    )
    assert url.startswith("/api/v2/")


def test_build_handle():
    handle = build_handle(request=_req())
    assert handle.workflow_id.startswith("deploy-acme-prod-")
    assert "/workflow_runs/" in handle.polling_url


# ---- rate limit ---------------------------------------------------


def test_ci_rate_limit_higher_than_manual():
    """CI tokens get higher RPM than operator-at-keyboard."""
    assert (
        rate_limit_for(kind=TriggerKind.CI)
        > rate_limit_for(kind=TriggerKind.MANUAL_CLI)
    )


def test_scheduled_rate_limit_lowest():
    """Scheduled triggers shouldn't burst — they're deterministic."""
    scheduled = rate_limit_for(kind=TriggerKind.SCHEDULED)
    for kind in [
        TriggerKind.CI, TriggerKind.MANUAL_CLI, TriggerKind.WEBHOOK_SCM,
    ]:
        assert scheduled <= rate_limit_for(kind=kind)


def test_rate_limit_for_every_trigger():
    """Every TriggerKind enum value has a rate limit registered."""
    for kind in TriggerKind:
        assert kind in RATE_LIMIT_PER_MINUTE
        assert rate_limit_for(kind=kind) > 0
