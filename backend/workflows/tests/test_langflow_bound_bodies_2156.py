"""Source bindings retain exact ports, finite serial order and authoring identity."""

import copy
import dataclasses
import hashlib
import json
import uuid

import pytest

from workflows.importers.base import FlowImportError
from workflows.importers.langflow import LangflowImporter
from workflows.manifest import emit_workflow_manifest, parse_workflow_manifest
from workflows.services.dsl_parser import emit_workflows_dsl, parse_workflows_dsl
from workflows.source_ports import (
    SourcePortContractError,
    imported_input,
    imported_output,
    validate_source_ports,
)
from workflows.tests.test_guid_target_references_2156 import targets as targets
from workflows.tests.test_langflow_collections_2156 import source_collection


def bound_source(target_guid, *, component="RunFlow"):
    source = source_collection(["first", "second"])
    data = source["data"]
    key = "Parser-body"
    input_port = "child-entry~input_value" if component == "RunFlow" else "input_value"
    output_port = "child-reply~message" if component == "RunFlow" else "response"
    node = data["nodes"][2]
    node["data"]["type"] = component
    node["data"]["node"] = {
        "template": {
            "flow_id_selected": {"value": str(uuid.uuid4())},
            "n_messages": {"value": 0},
        },
        "outputs": [{"name": output_port, "types": ["Message"]}],
    }
    for edge, field, port in (
        (data["edges"][1], "targetHandle", input_port),
        (data["edges"][2], "sourceHandle", output_port),
    ):
        edge[field] = json.dumps({"id": key, "fieldName" if field == "targetHandle" else "name": port})
    source["astrolift_bindings"] = {
        key: {
            "target_guid": str(target_guid),
            "source_node_digest": hashlib.sha256(
                json.dumps(node, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest(),
            "input_key": "input_value",
            "output_path": "text",
            "output_mode": "message",
        }
    }
    return source


@pytest.mark.parametrize("component,kind", [("RunFlow", "workflow"), ("Agent", "agent_dispatch")])
def test_exact_source_body_ports_bind_to_native_guid_and_roundtrip(component, kind):
    guid = uuid.uuid4()
    result = LangflowImporter().import_flow(bound_source(guid, component=component))
    owner, body = result.manifest.stages
    assert owner.kind == "collection" and body.kind == kind
    assert (body.workflow if kind == "workflow" else body.agent) == f"guid:{guid}"
    assert body.iteration["source_component"] == component
    assert (
        body.iteration["source_flow_guid"] is not None
        if component == "RunFlow"
        else body.iteration["source_flow_guid"] is None
    )
    assert owner.iteration["body_end"] == body.output_key
    assert parse_workflow_manifest(emit_workflow_manifest(result.manifest)) == result.manifest
    yaml = emit_workflows_dsl(
        [
            {
                "slug": "bound-source",
                "name": "Bound source",
                "pattern_kind": "chained",
                "stages": [
                    {
                        **dataclasses.asdict(stage),
                        "agent_ref": stage.agent or "",
                        "workflow_ref": stage.workflow or "",
                        "skill_refs": stage.skills,
                        "timeout_seconds": stage.timeout,
                        "environment_spec_slug": stage.environment_spec_slug or "",
                        "prompt": stage.prompt or "",
                    }
                    for stage in result.manifest.stages
                ],
            }
        ]
    )
    assert [stage["iteration"] for stage in parse_workflows_dsl(yaml)[0]["stages"]] == [
        owner.iteration,
        body.iteration,
    ]
    assert result.gaps[0].severity == "warning"
    ports = validate_source_ports(body.iteration, kind=kind)
    assert imported_input(ports, {"text": "exact input"}) == {"input_value": "exact input"}
    assert imported_output(ports, {"text": "exact output"}, timestamp="fixed") == {
        "text": "exact output",
        "timestamp": "fixed",
    }


@pytest.mark.parametrize(
    "change",
    [
        "missing_binding",
        "unbound_extra",
        "wrong_digest",
        "changed_source",
        "wrong_guid",
        "output_ambiguity",
        "wrong_type",
        "source_state",
        "bad_output_path",
        "reserved_input",
        "unknown_source_code",
    ],
)
def test_source_native_binding_ambiguity_and_stale_review_are_refused(change):
    source = bound_source(uuid.uuid4())
    node = source["data"]["nodes"][2]
    binding = source["astrolift_bindings"][node["id"]]
    if change == "missing_binding":
        source.pop("astrolift_bindings")
    elif change == "unbound_extra":
        source["astrolift_bindings"]["other-node"] = copy.deepcopy(binding)
    elif change == "wrong_digest":
        binding["source_node_digest"] = "0" * 64
    elif change == "changed_source":
        node["data"]["node"]["template"]["flow_id_selected"]["value"] = str(uuid.uuid4())
    elif change == "wrong_guid":
        binding["target_guid"] = "worker"
    elif change == "output_ambiguity":
        node["data"]["node"]["outputs"].insert(0, {"name": "other~output", "types": ["Message"]})
    elif change == "wrong_type":
        node["data"]["node"]["outputs"][0]["types"] = ["Data"]
    elif change == "source_state":
        node["data"]["node"]["template"]["session_id"] = {"value": "shared-session"}
    elif change == "bad_output_path":
        binding["output_path"] = "payload[0]"
    elif change == "reserved_input":
        binding["input_key"] = "_astrolift_workflow"
    else:
        node["data"]["node"]["template"]["code"] = {"value": "altered implementation"}
    if change in {"output_ambiguity", "wrong_type", "source_state"}:
        binding["source_node_digest"] = hashlib.sha256(
            json.dumps(node, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    with pytest.raises(FlowImportError):
        LangflowImporter().import_flow(source)


@pytest.mark.parametrize("result", [{}, {"text": 4}, {"text": "x" * 65537}])
def test_missing_or_invalid_native_message_output_never_becomes_successful_feedback(result):
    ports = LangflowImporter().import_flow(bound_source(uuid.uuid4())).manifest.stages[1].iteration
    with pytest.raises(SourcePortContractError):
        imported_output(ports, result, timestamp="fixed")


@pytest.mark.parametrize(
    "field", ["source_component", "output_mode", "native_input_key", "native_output_path", "target_guid"]
)
def test_malformed_source_contract_values_are_typed_refusals(field):
    ports = LangflowImporter().import_flow(bound_source(uuid.uuid4())).manifest.stages[1].iteration
    ports[field] = []
    with pytest.raises(SourcePortContractError):
        validate_source_ports(ports, kind="workflow")


@pytest.mark.parametrize("component", ["RunFlow", "Agent"])
def test_saved_source_contract_refuses_ports_outside_the_importers_supported_semantics(component):
    mapped = LangflowImporter().import_flow(bound_source(uuid.uuid4(), component=component)).manifest
    ports = mapped.stages[1].iteration
    if component == "RunFlow":
        ports["source_input_port"] = "child-entry~unsupported_input"
    else:
        ports.update(source_output_port="structured_response", output_mode="data")
    with pytest.raises(SourcePortContractError):
        validate_source_ports(ports, kind=mapped.stages[1].kind)
    with pytest.raises(ValueError):
        parse_workflow_manifest(emit_workflow_manifest(mapped))


@pytest.mark.django_db
def test_saved_native_source_agent_binding_cannot_substitute_a_different_live_target(request):
    from astrolift_workflows.activities.workflow_stage_activities import _get_workflow_stages_sync
    from workflows.manifest import create_definition_from_manifest

    selected = request.getfixturevalue("targets")
    mapped = LangflowImporter().import_flow(bound_source(selected.agents[1].guid, component="Agent")).manifest
    definition = create_definition_from_manifest(mapped, organization=selected.org, is_enabled=True)
    plan = _get_workflow_stages_sync(definition.slug, review_organization_id=selected.org.pk)
    assert plan["stages"][1]["agent_definition_id"] == selected.agents[1].pk
    with pytest.raises(RuntimeError, match="source ports differ from their explicit native target"):
        _get_workflow_stages_sync(
            definition.slug,
            review_organization_id=selected.org.pk,
            stage_bindings={"1": {"agent_workload_id": str(selected.agents[0].guid)}},
        )

    stage = definition.stages.get(order=1)
    stage.iteration = {**stage.iteration, "target_guid": str(selected.agents[0].guid)}
    stage.save(update_fields=["iteration"])
    with pytest.raises(RuntimeError, match="source ports differ from their explicit native target"):
        _get_workflow_stages_sync(definition.slug, review_organization_id=selected.org.pk)


@pytest.mark.django_db
def test_actual_http_import_retains_bound_target_and_ports_with_real_role_and_token_ceiling(settings):
    from django.contrib.auth import get_user_model
    from django.test import Client

    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken, Member, Organization, Role, RoleBinding
    from core.permissions import Permission
    from workflows.models import WorkflowDefinition, WorkflowStage

    org = Organization.objects.create(name="Source HTTP import", slug="source-bound-http")
    user = get_user_model().objects.create_user(username="source-bound-http-actor")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk)
    role = Role.objects.create(
        organization=org,
        name="Import",
        slug="source-bound-import",
        scope_level="ORG",
        permissions=[Permission.WORKFLOW_CREATE.value],
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.pk)
    target = WorkflowDefinition.objects.create(
        organization=org, name="Source target", slug="source-bound-target", model_label="", is_enabled=True
    )
    source = bound_source(target.guid)
    client = Client()
    query = 'mutation($payload:JSON!){importWorkflowFlow(format:"langflow",payload:$payload,preview:false){ok createdSlug stages{kind workflow iteration} gaps{code severity}}}'
    for scope in ("read:apps", "workflow:write"):
        issued = mint_token()
        ApiToken.objects.create(
            user=user, organization=org, name="Source importer", token_hash=issued.token_hash, scopes=[scope]
        )
        response = client.post(
            f"/{settings.BASE_URL}gql/config/",
            data={"query": query, "variables": {"payload": source}},
            content_type="application/json",
            HTTP_X_ASTROLIFT_ORGANIZATION=str(org.guid),
            HTTP_AUTHORIZATION=f"Bearer {issued.plaintext}",
        )
        assert response.status_code == 200
        payload = response.json()
        if scope == "read:apps":
            assert payload.get("errors") and WorkflowDefinition.objects.count() == 1
        else:
            assert not payload.get("errors")
            result = payload["data"]["importWorkflowFlow"]
            assert result["ok"] and result["stages"][1]["workflow"] == f"guid:{target.guid}"
            definition = WorkflowDefinition.objects.get(organization=org, slug=result["createdSlug"])
            assert not definition.is_enabled
            stage = WorkflowStage.objects.get(definition=definition, order=1)
            assert stage.iteration == result["stages"][1]["iteration"]
            assert stage.iteration["target_guid"] == str(target.guid)
