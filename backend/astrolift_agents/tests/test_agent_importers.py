from __future__ import annotations

import pytest

from astrolift_agents.models import Brief
from astrolift_agents.services.agent_importers import AgentImportError, import_agent_spec
from astrolift_agents.services.imported_agent_registration import persist_imported_agent_package
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import AppTeamAccess, RegisteredApp, Workload


def _langflow():
    return {
        "name": "Incident flow",
        "data": {
            "nodes": [
                {"id": "input", "data": {"type": "ChatInput"}},
                {
                    "id": "agent",
                    "data": {"type": "Agent", "node": {"display_name": "Triage incident"}},
                },
                {
                    "id": "tool",
                    "data": {"type": "SearchTool", "node": {"display_name": "Jira Search"}},
                },
                {"id": "output", "data": {"type": "ChatOutput"}},
            ],
            "edges": [
                {"source": "input", "target": "agent"},
                {"source": "tool", "target": "agent"},
                {"source": "agent", "target": "output"},
            ],
        },
    }


def test_agents_md_requires_runtime_before_it_is_runnable():
    result = import_agent_spec(
        "agents.md",
        {"name": "Review bot", "content": "Review changes and report findings."},
    )
    assert result.package["schema"] == "astrolift.agent.package/v1"
    assert result.runnable is False
    assert [gap.code for gap in result.gaps] == ["runtime_image_required"]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"runtime_image": {"unexpected": "object"}}, "runtime_image must be a string"),
        ({"timeout_seconds": "300"}, "timeout_seconds must be an integer"),
        ({"timeout_seconds": 0}, "timeout_seconds must be between"),
        ({"timeout_seconds": 604801}, "timeout_seconds must be between"),
    ],
)
def test_import_options_fail_with_validation_errors(options, message):
    with pytest.raises(AgentImportError, match=message):
        import_agent_spec(
            "agents_md",
            "Review changes and report findings.",
            options=options,
        )


def test_langflow_can_flatten_to_a_runnable_package_without_hiding_gaps():
    result = import_agent_spec(
        "langflow",
        _langflow(),
        options={"runtime_image": "example/agent:sha-123"},
    )
    assert result.runnable is True
    assert result.package["federation"]["source_format"] == "langflow"
    assert result.package["tools"][0]["slug"] == "jira-search"
    codes = {gap.code for gap in result.gaps}
    assert {"flow_input", "flow_output", "tool_bindings_unresolved"} <= codes


def test_flow_federation_preview_is_blocked_until_stage_bindings_exist():
    result = import_agent_spec(
        "langflow",
        _langflow(),
        options={"runtime_image": "example/agent:sha-123", "mode": "federation"},
    )
    assert result.runnable is False
    assert any(gap.code == "stage_bindings_required" and gap.blocking for gap in result.gaps)


def test_native_package_rejects_unsafe_source_paths():
    with pytest.raises(AgentImportError, match="may not"):
        import_agent_spec(
            "astrolift_package",
            {
                "schema": "astrolift.agent.package/v1",
                "agent": {"name": "unsafe"},
                "source": {
                    "root": "../private",
                    "manifest_path": "agent.json",
                    "path_mode": "chroot",
                },
                "runtime": {"image": "example/agent:1"},
            },
        )


@pytest.mark.django_db
def test_runnable_import_persists_and_reconciles_one_direct_upload_agent():
    org = Organization.objects.create(name="Import Org", slug="import-org")
    team = Team.objects.create(organization=org, name="Import Team", slug="import-team")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Import Project",
        slug="import-project",
    )
    first = import_agent_spec(
        "agents_md",
        {"name": "Review bot", "content": "Review changes."},
        options={"runtime_image": "example/agent:sha-1", "timeout_seconds": 600},
    )
    registration = persist_imported_agent_package(
        project=project,
        package=first.package,
        slug="review-bot",
    )

    assert registration.created is True
    assert registration.app.source_kind == RegisteredApp.SourceKind.DIRECT_UPLOAD
    assert AppTeamAccess.objects.filter(
        registered_app=registration.app,
        team=team,
        access_level=AppTeamAccess.AccessLevel.OWNER.value,
        deleted_at__isnull=True,
    ).exists()
    assert registration.workload.kind == Workload.Kind.AGENT
    assert registration.workload.tool_timeout_seconds == 600
    assert registration.workload.containers.get(is_primary=True).image_ref == "example/agent:sha-1"
    assert registration.environment_spec.slug == "review-bot"
    first_brief_id = registration.brief.pk

    second = import_agent_spec(
        "agents_md",
        {"name": "Review bot", "content": "Review changes adversarially."},
        options={"runtime_image": "example/agent:sha-2"},
    )
    updated = persist_imported_agent_package(
        project=project,
        package=second.package,
        slug="review-bot",
    )

    assert updated.created is False
    assert updated.app.pk == registration.app.pk
    assert updated.brief.pk != first_brief_id
    assert Brief.objects.filter(organization=org).count() == 2
    assert updated.workload.containers.get(is_primary=True).image_ref == "example/agent:sha-2"
