from __future__ import annotations

import pytest

from astrolift_agents.services.agent_package import (
    PACKAGE_SCHEMA,
    AgentPackageError,
    build_agent_package,
    compatibility_snapshot,
    project_manifest_environment,
    source_slice_from_manifest,
    validate_agent_package,
)


def test_source_slice_is_chrooted_and_allows_explicit_shared_mounts():
    source = source_slice_from_manifest(
        manifest_path="agents/triage/astrolift.toml",
        package_config={
            "root": "runtime",
            "include": ["brief/**", "skills/**", "bin/agent-report"],
            "exclude": ["**/*.tmp"],
            "shared": [
                {"source": "packs/service-access", "mount": "shared/service-access"},
            ],
        },
    )

    assert source == {
        "root": "agents/triage/runtime",
        "manifest_path": "agents/triage/astrolift.toml",
        "include": ["brief/**", "skills/**", "bin/agent-report"],
        "exclude": ["**/*.tmp"],
        "executables": [],
        "shared": [
            {"source": "packs/service-access", "mount": "shared/service-access"},
        ],
        "path_mode": "chroot",
    }


@pytest.mark.parametrize(
    ("field", "package"),
    [
        ("root", {"root": "../other-agent"}),
        ("include", {"include": ["/etc/passwd"]}),
        ("shared", {"shared": [{"source": "../private", "mount": "shared/x"}]}),
        (
            "shared",
            {
                "shared": [
                    {"source": "packs/a", "mount": "shared/x"},
                    {"source": "packs/b", "mount": "shared/x"},
                ]
            },
        ),
    ],
)
def test_source_slice_rejects_escape_and_ambiguous_mounts(field, package):
    with pytest.raises(AgentPackageError, match=field):
        source_slice_from_manifest(
            manifest_path="agents/triage/astrolift.toml",
            package_config=package,
        )


def test_package_ir_composes_brief_skills_and_compatibility_projection():
    package = build_agent_package(
        agent_name="triage",
        manifest_path="agents/triage/astrolift.toml",
        package_config={"root": "."},
        brief_text="Process one queue batch.",
        context_files={"agents/triage/brief/policy.md": "Never copy PHI."},
        skills=[
            {
                "slug": "emr-triage",
                "name": "EMR Triage",
                "instructions": "Inspect reports read-only.",
            }
        ],
        tools=[{"slug": "agent-report", "adapter": "command"}],
        environment={"MAX_TICKETS_PER_RUN": "10"},
        secret_refs=[{"env_var": "JIRA_API_TOKEN", "uri": "smd-jira-agent-api-token"}],
        runtime={"image": "example/agent:sha-123"},
        execution={"timeout_seconds": 900},
        imports=[
            {
                "format": "LangFlow",
                "path": "imports/triage.json",
                "mode": "workflow",
            }
        ],
        federation={
            "schema": "astrolift.agent.federation/v1",
            "manifest_path": "astrolift.agents.toml",
            "anchor_manifest": "agents/triage/astrolift.toml",
        },
    )

    assert package["schema"] == PACKAGE_SCHEMA
    assert package["source"]["root"] == "agents/triage"
    assert package["prompt"]["system"] == (
        "# Agent brief\n\nProcess one queue batch.\n\n---\n\n"
        "# Skill: EMR Triage\n\nInspect reports read-only."
    )
    assert package["imports"] == [{"format": "langflow", "path": "imports/triage.json", "mode": "workflow"}]
    assert package["federation"]["manifest_path"] == "astrolift.agents.toml"
    snapshot = compatibility_snapshot(package)
    assert snapshot["agent_package"] == package
    assert snapshot["tools"] == ["agent-report"]
    assert snapshot["env_vars"] == {"MAX_TICKETS_PER_RUN": "10"}
    assert snapshot["source_slice"]["path_mode"] == "chroot"


@pytest.mark.parametrize(
    ("imports", "federation", "message"),
    [
        ([{"path": "flow.json"}], {}, "format is required"),
        ([{"format": "langflow", "path": "../flow.json"}], {}, "may not"),
        ([{"format": "langflow", "mode": "magic"}], {}, "mode must"),
        ([], {"manifest_path": "../bundle.toml"}, "may not"),
    ],
)
def test_package_rejects_unsafe_import_and_federation_metadata(imports, federation, message):
    with pytest.raises(AgentPackageError, match=message):
        build_agent_package(
            agent_name="triage",
            manifest_path="agents/triage/astrolift.toml",
            package_config={},
            brief_text="",
            context_files={},
            skills=[],
            tools=[],
            environment={},
            secret_refs=[],
            imports=imports,
            federation=federation,
        )


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"values": [], "secret_refs": []}, "environment.values must be an object"),
        ({"values": {"BAD-NAME": "x"}}, "invalid environment variable name"),
        ({"values": {"AGENT_CALLBACK_URL": "https://attacker.invalid"}}, "dispatcher-owned"),
        ({"values": {"NESTED": {"not": "a scalar"}}}, "must be a scalar or null"),
        ({"values": {}, "secret_refs": {}}, "environment.secret_refs must be an array"),
        (
            {
                "values": {},
                "secret_refs": [{"env_var": "ASTROLIFT_CLUSTER_KEY", "uri": "sm:override"}],
            },
            "dispatcher-owned",
        ),
        (
            {"values": {}, "secret_refs": [{"env_var": "TOKEN", "uri": "sm:x", "value": "leak"}]},
            "unsupported fields: value",
        ),
        (
            {
                "values": {},
                "secret_refs": [
                    {"env_var": "TOKEN", "uri": "sm:a"},
                    {"env_var": " TOKEN ", "uri": "sm:b"},
                ],
            },
            "duplicate environment variable",
        ),
    ],
)
def test_native_package_rejects_malformed_environment_contract(environment, message):
    package = build_agent_package(
        agent_name="triage",
        manifest_path="astrolift.toml",
        package_config={},
        brief_text="",
        context_files={},
        skills=[],
        tools=[],
        environment={},
        secret_refs=[],
    )
    package["environment"] = environment

    with pytest.raises(AgentPackageError, match=message):
        validate_agent_package(package)


@pytest.mark.parametrize(
    ("section", "value", "message"),
    [
        ("runtime", {"image": ["not", "a", "string"]}, "runtime.image must be a string"),
        ("execution", {"timeout_seconds": "300"}, "timeout_seconds must be an integer"),
        ("execution", {"timeout_seconds": 0}, "timeout_seconds must be between"),
        ("execution", {"timeout_seconds": 604801}, "timeout_seconds must be between"),
    ],
)
def test_native_package_rejects_malformed_runtime_and_execution(section, value, message):
    package = build_agent_package(
        agent_name="triage",
        manifest_path="astrolift.toml",
        package_config={},
        brief_text="",
        context_files={},
        skills=[],
        tools=[],
        environment={},
        secret_refs=[],
    )
    package[section] = value

    with pytest.raises(AgentPackageError, match=message):
        validate_agent_package(package)


def test_package_normalizes_environment_names_and_secret_references():
    package = build_agent_package(
        agent_name="triage",
        manifest_path="astrolift.toml",
        package_config={},
        brief_text="",
        context_files={},
        skills=[],
        tools=[],
        environment={" MAX_TICKETS ": 10, "OPTIONAL": None},
        secret_refs=[{"env_var": " JIRA_TOKEN ", "uri": " sm:jira "}],
    )

    assert package["environment"] == {
        "values": {"MAX_TICKETS": 10, "OPTIONAL": None},
        "secret_refs": [{"env_var": "JIRA_TOKEN", "uri": "sm:jira"}],
    }


def test_manifest_environment_projection_accepts_reference_forms():
    values, refs = project_manifest_environment(
        {"tool_preset": "emr-read", "allow_install": False, "BATCH_SIZE": 10},
        {
            "JIRA_TOKEN": "sm:jira",
            "EMR_TOKEN": {"secret_name": "sm:emr"},
            "CANVAS_TOKEN": {"uri": "sm:canvas"},
        },
    )

    assert values == {"BATCH_SIZE": 10}
    assert refs == [
        {"env_var": "JIRA_TOKEN", "uri": "sm:jira"},
        {"env_var": "EMR_TOKEN", "uri": "sm:emr"},
        {"env_var": "CANVAS_TOKEN", "uri": "sm:canvas"},
    ]


@pytest.mark.parametrize(
    ("environment", "secrets", "message"),
    [
        ({"AGENT_DISPATCH_URL": "https://attacker.invalid"}, {}, "dispatcher-owned"),
        ({}, {"TOKEN": {"secret_name": "sm:x", "value": "plaintext"}}, "unsupported fields"),
        ({}, {"TOKEN": {"secret_name": "sm:x", "uri": "sm:y"}}, "exactly one"),
        ({}, {"TOKEN": {"value": "plaintext"}}, "unsupported fields"),
    ],
)
def test_manifest_environment_projection_rejects_unsafe_source_contract(environment, secrets, message):
    with pytest.raises(AgentPackageError, match=message):
        project_manifest_environment(environment, secrets)
