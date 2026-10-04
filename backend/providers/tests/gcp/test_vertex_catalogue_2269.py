"""Actual generated GAPIC clients/protobuf wire; no cloud or credential calls."""

import logging
from dataclasses import replace
from typing import Any

import grpc
import pytest
from google.api_core.exceptions import NotFound, PermissionDenied, ServiceUnavailable, Unauthenticated
from google.auth.credentials import AnonymousCredentials
from google.cloud import aiplatform_v1 as vertex
from google.cloud import resourcemanager_v3 as manager
from google.cloud.aiplatform_v1.services.endpoint_service.transports.grpc import EndpointServiceGrpcTransport
from google.cloud.aiplatform_v1.services.model_service.transports.grpc import ModelServiceGrpcTransport
from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

from _sdk.cloud_credentials import CloudCredential, CredentialMode
from gcp.vertex_catalogue import (
    MAX_RESPONSE_BYTES,
    CatalogueState,
    VertexCatalogue,
    VertexCatalogueConfig,
    VertexCatalogueError,
)

PROJECT_ID = "fixture-project"
PROJECT_NUMBER = "415104041262"
PARENT = f"projects/{PROJECT_NUMBER}/locations/us-central1"
ENDPOINT = f"{PARENT}/endpoints/73182"
MODEL = f"{PARENT}/models/model_401"
CONFIG = VertexCatalogueConfig(PROJECT_ID, "us-central1", CloudCredential("gcp", declared_account=PROJECT_ID))


class Wire(grpc.Channel):
    """Generated serializers on actual GAPIC transports with a no-network channel."""

    def __init__(self):
        self.calls: list[tuple[str, Any]] = []
        self.closed = 0
        self.project = manager.Project(
            name=f"projects/{PROJECT_NUMBER}", project_id=PROJECT_ID, state=manager.Project.State.ACTIVE
        )
        self.endpoint = vertex.Endpoint(
            name=ENDPOINT,
            display_name="display differs from native ID",
            deployed_models=[
                vertex.DeployedModel(
                    id="8102",
                    model=MODEL,
                    model_version_id="7",
                    dedicated_resources={
                        "min_replica_count": 2,
                        "max_replica_count": 4,
                        "machine_spec": {"machine_type": "n1-standard-4"},
                    },
                    status={"available_replica_count": 2},
                )
            ],
            traffic_split={"8102": 100},
        )
        self.model = vertex.Model(
            name=MODEL, display_name="registered model", version_id="7", version_aliases=["default", "production"]
        )
        self.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[self.endpoint])]
        self.model_pages = [vertex.ListModelsResponse(models=[self.model])]
        self.project_reads = 0
        self.project_change_at = 0
        self.error: Exception | None = None
        self.error_method = "ListEndpoints"
        self.clients = (
            manager.ProjectsClient(
                transport=ProjectsGrpcTransport(
                    channel=lambda *args, **kwargs: self,
                    host="cloudresourcemanager.googleapis.com",
                    credentials=AnonymousCredentials(),
                )
            ),
            vertex.EndpointServiceClient(
                transport=EndpointServiceGrpcTransport(
                    channel=lambda *args, **kwargs: self,
                    host="us-central1-aiplatform.googleapis.com",
                    credentials=AnonymousCredentials(),
                )
            ),
            vertex.ModelServiceClient(
                transport=ModelServiceGrpcTransport(
                    channel=lambda *args, **kwargs: self,
                    host="us-central1-aiplatform.googleapis.com",
                    credentials=AnonymousCredentials(),
                )
            ),
        )

    def subscribe(self, callback, try_to_connect=False):
        raise AssertionError("no connectivity subscriptions")

    def unsubscribe(self, callback):
        raise AssertionError("no connectivity subscriptions")

    def unary_stream(self, *args, **kwargs):
        raise AssertionError("no streaming operations")

    stream_unary = unary_stream
    stream_stream = unary_stream

    def close(self):
        self.closed += 1

    def unary_unary(self, method, request_serializer=None, response_deserializer=None, *args, **kwargs):
        name = (method.decode() if isinstance(method, bytes) else method).rsplit("/", 1)[-1]
        request_types = {
            "GetProject": manager.GetProjectRequest,
            "ListEndpoints": vertex.ListEndpointsRequest,
            "GetEndpoint": vertex.GetEndpointRequest,
            "ListModels": vertex.ListModelsRequest,
            "GetModel": vertex.GetModelRequest,
        }

        def call(request, **kwargs):
            assert name in request_types, "No write, IAM search or LRO operation"
            typed = request_types[name].deserialize(request_serializer(request))
            self.calls.append((name, typed))
            assert 0 < kwargs["timeout"] <= 10
            if self.error and name == self.error_method:
                raise self.error
            if name == "GetProject":
                self.project_reads += 1
                assert typed.name in {f"projects/{PROJECT_ID}", f"projects/{PROJECT_NUMBER}"}
                if self.project_change_at and self.project_reads >= self.project_change_at:
                    self.project.name = "projects/987654321"
                response = self.project
            elif name in {"ListEndpoints", "ListModels"}:
                assert typed.parent == PARENT and not typed.filter and not typed.order_by
                assert "labels" not in typed.read_mask.paths and "artifact_uri" not in typed.read_mask.paths
                response = (self.endpoint_pages if name == "ListEndpoints" else self.model_pages).pop(0)
            elif name == "GetEndpoint":
                assert typed.name == ENDPOINT
                response = self.endpoint
            else:
                assert typed.name == MODEL
                response = self.model
            return response_deserializer(type(response).serialize(response))

        class CompletedCall:
            def trailing_metadata(self):
                return ()

        call.with_call = lambda request, **kwargs: (call(request, **kwargs), CompletedCall())
        return call

    def catalogue(self, config=CONFIG):
        return VertexCatalogue(config, clients=self.clients)


@pytest.mark.parametrize("kind", ["endpoints", "models"])
def test_actual_gapic_inventory_separate_safe_metadata(kind):
    wire = Wire()
    wire.endpoint.labels["private"] = "PRIVATE_METADATA_MARKER"
    wire.model.labels["private"] = "PRIVATE_METADATA_MARKER"
    wire.model.artifact_uri = "gs://private/path"
    wire.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[wire.endpoint])]
    wire.model_pages = [vertex.ListModelsResponse(models=[wire.model])]
    result = getattr(wire.catalogue(), kind)()
    assert result.state == CatalogueState.METADATA and len(result.items) == 1 and not result.truncated
    assert result.identity.project_id == PROJECT_ID and result.identity.project_number == PROJECT_NUMBER
    assert wire.project_reads == 3 and result.items[0].invoke_access == "unknown"
    assert "PRIVATE_METADATA_MARKER" not in repr(result) and "gs://" not in repr(result)
    if kind == "endpoints":
        assert result.items[0].name == ENDPOINT and result.items[0].kind == "endpoint"
        deployed = result.items[0].deployments[0]
        assert (deployed.id, deployed.model_version_id, deployed.traffic_percent, deployed.min_replicas) == (
            "8102",
            "7",
            100,
            2,
        )
    else:
        assert result.items[0].version_id == "7" and result.items[0].version_aliases == ("default", "production")
        assert result.items[0].kind == "registered_model" and not result.items[0].deployments


@pytest.mark.parametrize("kind", ["endpoints", "models"])
def test_actual_gapic_explicit_pagination_identity_each_page(kind):
    wire = Wire()
    if kind == "endpoints":
        wire.endpoint_pages = [
            vertex.ListEndpointsResponse(endpoints=[wire.endpoint], next_page_token="native-token"),
            vertex.ListEndpointsResponse(endpoints=[vertex.Endpoint(name=f"{PARENT}/endpoints/123")]),
        ]
    else:
        wire.model_pages = [
            vertex.ListModelsResponse(models=[wire.model], next_page_token="native-token"),
            vertex.ListModelsResponse(models=[vertex.Model(name=f"{PARENT}/models/123")]),
        ]
    result = getattr(wire.catalogue(), kind)(limit=2)
    assert result.state == CatalogueState.METADATA and len(result.items) == 2
    assert [name for name, _ in wire.calls] == [
        "GetProject",
        "GetProject",
        "List" + kind.title(),
        "GetProject",
        "GetProject",
        "List" + kind.title(),
        "GetProject",
    ]
    assert wire.calls[5][1].page_token == "native-token"


@pytest.mark.parametrize("kind", ["endpoints", "models"])
@pytest.mark.parametrize("cap", ["items", "pages"])
def test_truncation_explicit_no_native_token_returned(kind, cap):
    wire = Wire()
    if kind == "endpoints":
        wire.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[wire.endpoint], next_page_token="PRIVATE_TOKEN")]
    else:
        wire.model_pages = [vertex.ListModelsResponse(models=[wire.model], next_page_token="PRIVATE_TOKEN")]
    result = getattr(wire.catalogue(), kind)(limit=1 if cap == "items" else 100, max_pages=1)
    assert result.state == CatalogueState.METADATA and result.truncated and len(result.items) == 1
    assert "PRIVATE_TOKEN" not in repr(result)


@pytest.mark.parametrize("phase", [1, 2, 3, 4, 5])
def test_changed_mapping_discards_metadata_and_stops_next_native_read(phase):
    wire = Wire()
    wire.project_change_at = phase
    wire.endpoint_pages = [
        vertex.ListEndpointsResponse(endpoints=[wire.endpoint], next_page_token="page2"),
        vertex.ListEndpointsResponse(),
    ]
    config = replace(CONFIG, project=PROJECT_NUMBER, credential=CloudCredential("gcp", declared_account=PROJECT_NUMBER))
    result = wire.catalogue(config).endpoints()
    assert result.state == CatalogueState.REFUSED and not result.items and result.identity is None
    assert wire.project_reads == phase
    assert len([name for name, _ in wire.calls if name == "ListEndpoints"]) == (
        0 if phase <= 2 else 1 if phase <= 4 else 2
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "projects/0"),
        ("name", "projects/unproved"),
        ("project_id", "foreign-project"),
        ("state", manager.Project.State.DELETE_REQUESTED),
        ("state", manager.Project.State.STATE_UNSPECIFIED),
    ],
)
def test_missing_foreign_or_inactive_project_mapping_before_vertex(field, value):
    wire = Wire()
    setattr(wire.project, field, value)
    result = wire.catalogue().models()
    assert result.state == CatalogueState.REFUSED and not result.items
    assert [name for name, _ in wire.calls] == ["GetProject"]


@pytest.mark.parametrize("kind", ["endpoints", "models"])
@pytest.mark.parametrize("project", [PROJECT_ID, PROJECT_NUMBER])
def test_confirmed_id_number_spellings_and_fresh_detail(kind, project):
    wire = Wire()
    row = wire.endpoint if kind == "endpoints" else wire.model
    row.name = row.name.replace(PROJECT_NUMBER, project)
    wire.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[wire.endpoint])]
    wire.model_pages = [vertex.ListModelsResponse(models=[wire.model])]
    config = replace(CONFIG, project=project, credential=CloudCredential("gcp", declared_account=project))
    result = getattr(wire.catalogue(config), kind)()
    assert result.state == CatalogueState.METADATA and result.items[0].name == row.name


@pytest.mark.parametrize("method,name", [("endpoint_detail", ENDPOINT), ("model_detail", MODEL)])
def test_fresh_detail_reads_only_and_rechecks_mapping(method, name):
    wire = Wire()
    result = getattr(wire.catalogue(), method)(name)
    assert result.state == CatalogueState.METADATA and len(result.items) == 1
    assert [call for call, _ in wire.calls] == [
        "GetProject",
        "GetEndpoint" if method == "endpoint_detail" else "GetModel",
        "GetProject",
    ]


@pytest.mark.parametrize(
    "name",
    [
        MODEL + "@default",
        MODEL + "@7",
        "https://attacker/",
        MODEL.replace("us-central1", "us-east1"),
        MODEL.replace("models/", "endpoints/"),
    ],
)
def test_bad_or_alias_detail_zero_native_requests(name):
    wire = Wire()
    result = wire.catalogue().model_detail(name)
    assert result.state == CatalogueState.REFUSED and wire.calls == []


@pytest.mark.parametrize("kind", ["endpoints", "models"])
@pytest.mark.parametrize("change", ["project", "region", "kind"])
def test_foreign_returned_identity_refuses(kind, change):
    wire = Wire()
    row = wire.endpoint if kind == "endpoints" else wire.model
    row.name = (
        row.name.replace(PROJECT_NUMBER, "999999")
        if change == "project"
        else row.name.replace("us-central1", "us-east1")
        if change == "region"
        else row.name.replace(f"/{kind}/", "/operations/")
    )
    wire.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[wire.endpoint])]
    wire.model_pages = [vertex.ListModelsResponse(models=[wire.model])]
    result = getattr(wire.catalogue(), kind)()
    assert result.state == CatalogueState.REFUSED and not result.items


@pytest.mark.parametrize(
    "case",
    [
        "deployments",
        "aliases",
        "foreign_model",
        "duplicate_deployment",
        "traffic",
        "display",
        "bytes",
        "page",
        "repeated_token",
        "oversized_token",
        "duplicate_resource",
    ],
)
def test_bounded_payloads_and_native_continuation(case):
    wire = Wire()
    kind = "endpoints"
    if case == "deployments":
        wire.endpoint.deployed_models.extend([vertex.DeployedModel(id=str(i + 9000), model=MODEL) for i in range(65)])
    elif case == "aliases":
        kind = "models"
        wire.model.version_aliases.extend(["alias"] * 33)
    elif case == "foreign_model":
        wire.endpoint.deployed_models[0].model = MODEL.replace(PROJECT_NUMBER, "999999")
    elif case == "duplicate_deployment":
        wire.endpoint.deployed_models.append(wire.endpoint.deployed_models[0])
    elif case == "traffic":
        wire.endpoint.traffic_split["foreign"] = 100
    elif case == "display":
        wire.endpoint.display_name = "x" * 257
    elif case == "bytes":
        wire.endpoint.description = "PRIVATE" * MAX_RESPONSE_BYTES
    elif case == "page":
        wire.endpoint_pages[0].endpoints.extend([wire.endpoint] * 100)
    elif case == "repeated_token":
        wire.endpoint_pages = [
            vertex.ListEndpointsResponse(next_page_token="same"),
            vertex.ListEndpointsResponse(next_page_token="same"),
        ]
    elif case == "oversized_token":
        wire.endpoint_pages[0].next_page_token = "x" * 4097
    elif case == "duplicate_resource":
        alias = vertex.Endpoint(wire.endpoint)
        alias.name = alias.name.replace(PROJECT_NUMBER, PROJECT_ID)
        wire.endpoint_pages[0].endpoints.extend([alias])
    if case not in {"page", "repeated_token", "oversized_token", "duplicate_resource"}:
        wire.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[wire.endpoint])]
        wire.model_pages = [vertex.ListModelsResponse(models=[wire.model])]
    result = getattr(wire.catalogue(), kind)()
    assert result.state == CatalogueState.REFUSED and not result.items
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize(
    "exception,state",
    [
        (PermissionDenied, CatalogueState.DENIED),
        (Unauthenticated, CatalogueState.DENIED),
        (NotFound, CatalogueState.NOT_FOUND),
        (ServiceUnavailable, CatalogueState.ERROR),
        (RuntimeError, CatalogueState.ERROR),
    ],
)
@pytest.mark.parametrize("where", ["GetProject", "ListEndpoints"])
def test_provider_errors_safe_never_verified_empty(exception, state, where):
    wire = Wire()
    wire.error = exception("PRIVATE_PROVIDER_BODY_TOKEN")
    wire.error_method = where
    result = wire.catalogue().endpoints()
    assert result.state == state and not result.items and result.identity is None
    assert "PRIVATE_PROVIDER_BODY_TOKEN" not in repr(result)
    assert len([name for name, _ in wire.calls if name == where]) == 1


@pytest.mark.parametrize("limit,pages", [(0, 1), (501, 1), (True, 1), (1, 0), (1, 6), (1, True)])
def test_invalid_limits_before_any_sdk_call(limit, pages):
    wire = Wire()
    with pytest.raises(VertexCatalogueError, match="INVALID_LIMIT"):
        wire.catalogue().endpoints(limit=limit, max_pages=pages)
    assert not wire.calls


@pytest.mark.parametrize(
    "config",
    [
        replace(CONFIG, project="bad/url"),
        replace(CONFIG, region="https://attacker"),
        replace(CONFIG, credential=CloudCredential("aws", declared_account=PROJECT_ID)),
        replace(CONFIG, credential=CloudCredential("gcp", declared_account="foreign-project")),
        replace(
            CONFIG, credential=CloudCredential("gcp", mode=CredentialMode.AWS_ASSUME_ROLE, declared_account=PROJECT_ID)
        ),
        replace(CONFIG, credential=CloudCredential("gcp", declared_account=PROJECT_ID, role_arn="unproved")),
    ],
)
def test_invalid_source_configuration_no_adc_or_clients(config, monkeypatch):
    monkeypatch.setattr("google.auth.default", lambda **kwargs: pytest.fail("no credential construction"))
    with pytest.raises(VertexCatalogueError, match="INVALID_CONFIGURATION"):
        VertexCatalogue(config)


def test_injected_clients_caller_owned_close_refuses_reads():
    wire = Wire()
    catalogue = wire.catalogue()
    catalogue.close()
    result = catalogue.endpoints()
    assert result.state == CatalogueState.REFUSED and result.reason == "CLOSED"
    assert not wire.calls and wire.closed == 0


def test_production_factory_fixed_hosts_private_single_adc_bounded_channels_and_cleanup(monkeypatch, caplog):
    wire = Wire()
    wire.endpoint.labels["private"] = "PRIVATE_NATIVE_LABEL_MARKER"
    wire.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[wire.endpoint])]
    caplog.set_level(logging.DEBUG)
    for name in (
        "google.cloud.resourcemanager_v3.services.projects.transports.grpc",
        "google.cloud.aiplatform_v1.services.endpoint_service.transports.grpc",
        "google.cloud.aiplatform_v1.services.model_service.transports.grpc",
    ):
        caplog.set_level(logging.DEBUG, logger=name)
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", [caplog.handler])
    credential = AnonymousCredentials()
    factories = []
    adc = []

    def load(**kwargs):
        adc.append(kwargs)
        return credential, "unrelated-ambient-default-project"

    def create(host, **kwargs):
        factories.append((host, kwargs))
        return wire

    monkeypatch.setattr("google.auth.default", load)
    for transport in (ProjectsGrpcTransport, EndpointServiceGrpcTransport, ModelServiceGrpcTransport):
        monkeypatch.setattr(transport, "create_channel", staticmethod(create))
    monkeypatch.setenv("GOOGLE_API_USE_MTLS_ENDPOINT", "always")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "unrelated-ambient-default-project")
    with VertexCatalogue(CONFIG) as catalogue:
        catalogue._native()
        for name in (
            "google.cloud.resourcemanager_v3.services.projects.transports.grpc",
            "google.cloud.aiplatform_v1.services.endpoint_service.transports.grpc",
            "google.cloud.aiplatform_v1.services.model_service.transports.grpc",
        ):
            caplog.set_level(logging.DEBUG, logger=name)
            monkeypatch.setattr(logging.getLogger(name), "handlers", [caplog.handler])
        result = catalogue.endpoints()
        assert result.state == CatalogueState.METADATA
    assert adc == [{"scopes": ["https://www.googleapis.com/auth/cloud-platform.read-only"]}]
    assert [host for host, _ in factories] == [
        "cloudresourcemanager.googleapis.com",
        "us-central1-aiplatform.googleapis.com",
        "us-central1-aiplatform.googleapis.com",
    ]
    assert all(kwargs["credentials"] is credential for _, kwargs in factories)
    assert all(
        kwargs["options"] == [("grpc.max_receive_message_length", MAX_RESPONSE_BYTES)] for _, kwargs in factories
    )
    assert wire.closed == 3
    assert "PRIVATE_NATIVE_LABEL_MARKER" not in str([record.__dict__ for record in caplog.records])
    assert "credential" not in repr(result) and "unrelated-ambient" not in repr(result)


def test_foreign_detail_does_not_send_vertex_read_after_verified_mapping():
    wire = Wire()
    result = wire.catalogue().model_detail(MODEL.replace(PROJECT_NUMBER, "999999"))
    assert result.state == CatalogueState.REFUSED and not result.items
    assert [name for name, _ in wire.calls] == ["GetProject"]


@pytest.mark.parametrize("method,name", [("endpoint_detail", ENDPOINT), ("model_detail", MODEL)])
def test_detail_project_mapping_changed_after_native_read_discards_row(method, name):
    wire = Wire()
    wire.project_change_at = 2
    result = getattr(wire.catalogue(), method)(name)
    assert result.state == CatalogueState.REFUSED and not result.items
    assert wire.project_reads == 2


@pytest.mark.parametrize("kind", ["endpoints", "models"])
def test_verified_empty_is_distinct_from_error(kind):
    wire = Wire()
    wire.endpoint_pages = [vertex.ListEndpointsResponse()]
    wire.model_pages = [vertex.ListModelsResponse()]
    result = getattr(wire.catalogue(), kind)()
    assert result.state == CatalogueState.METADATA and result.identity is not None
    assert not result.items and not result.truncated


@pytest.mark.parametrize("suffix", ["@7", "@production", ""])
def test_endpoint_native_version_reference_literal_never_adopted(suffix):
    wire = Wire()
    wire.endpoint.deployed_models[0].model = MODEL + suffix
    wire.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[wire.endpoint])]
    result = wire.catalogue().endpoints()
    assert result.state == CatalogueState.METADATA
    deployed = result.items[0].deployments[0]
    assert deployed.model == MODEL + suffix and deployed.model_version_id == "7"
    assert result.items[0].invoke_access == "unknown"


def test_nondedicated_resources_not_fabricated_as_zero_request():
    wire = Wire()
    wire.endpoint.deployed_models[0].automatic_resources.min_replica_count = 1
    wire.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[wire.endpoint])]
    result = wire.catalogue().endpoints()
    assert result.state == CatalogueState.METADATA
    assert result.items[0].deployments[0].min_replicas is None
    assert result.items[0].deployments[0].max_replicas is None
    assert result.items[0].deployments[0].machine_type is None


def test_later_page_denial_discards_earlier_items_no_false_coverage():
    wire = Wire()
    wire.endpoint_pages = [vertex.ListEndpointsResponse(endpoints=[wire.endpoint], next_page_token="second")]
    original = wire.unary_unary

    def wrap(method, *args, **kwargs):
        call = original(method, *args, **kwargs)
        if method.endswith("ListEndpoints"):

            def denied_later(request, **params):
                if request.page_token:
                    raise PermissionDenied("PRIVATE_DENIED_MARKER")
                return call(request, **params)

            denied_later.with_call = lambda request, **params: (denied_later(request, **params), None)
            return denied_later
        return call

    # Construct new actual clients against the intercepted native channel.
    wire.unary_unary = wrap
    wire.clients = (
        wire.clients[0],
        vertex.EndpointServiceClient(
            transport=EndpointServiceGrpcTransport(channel=wire, host="us-central1-aiplatform.googleapis.com")
        ),
        wire.clients[2],
    )
    result = wire.catalogue().endpoints()
    assert result.state == CatalogueState.DENIED and not result.items and result.identity is None
    assert "PRIVATE_DENIED_MARKER" not in repr(result)
