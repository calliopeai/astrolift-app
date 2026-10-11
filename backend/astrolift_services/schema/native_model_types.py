"""Additive family metadata; the union does not enable native adoption."""

from datetime import datetime
from enum import Enum
from typing import Annotated

import strawberry

from astrolift_services.schema.bedrock_model_types import NativeModelConnectionSourceType


@strawberry.enum
class NativeModelFamily(Enum):
    BEDROCK = "bedrock"
    VERTEX = "vertex"
    FOUNDRY = "foundry"
    UNKNOWN = "unknown"


@strawberry.enum
class NativeModelConfigurationState(Enum):
    CONFIGURED = "configured"
    UNAVAILABLE = "unavailable"


@strawberry.enum
class NativeModelInvokeAccess(Enum):
    UNKNOWN = "unknown"


@strawberry.enum
class NativeModelConnectionKind(Enum):
    BEDROCK_FOUNDATION_MODEL = "bedrock_foundation_model"
    BEDROCK_INFERENCE_PROFILE = "bedrock_inference_profile"
    VERTEX_ENDPOINT = "vertex_endpoint"
    FOUNDRY_DEPLOYMENT = "foundry_deployment"
    UNKNOWN = "unknown"


@strawberry.type(name="VertexEndpointDeployedModelObservation")
class VertexEndpointDeployedModelObservationType:
    deployed_model_id: str
    model_resource_name: str
    model_version_id: str | None
    traffic_percent: int
    machine_type: str | None
    min_replicas: int | None
    max_replicas: int | None
    available_replicas: int | None


@strawberry.type(
    name="VertexEndpointConnectionSource",
    description="Endpoint routing metadata. IAM covers the whole Endpoint; Predict does not select one deployed model.",
)
class VertexEndpointConnectionSourceType:
    project_id: str
    project_number: str
    region: str
    endpoint_resource_name: str
    deployed_models: list[VertexEndpointDeployedModelObservationType]


@strawberry.type(
    name="FoundryDeploymentConnectionSource",
    description="Deployment metadata only; application adoption requires an independent gateway credential lifecycle.",
)
class FoundryDeploymentConnectionSourceType:
    subscription_id: str
    resource_group: str
    account_resource_id: str
    region: str
    account_kind: str
    local_auth_disabled: bool | None
    deployment_resource_id: str
    deployment_name: str
    model_format: str
    model_name: str
    model_version: str | None
    declared_sku: str | None
    declared_capacity: int | None
    provisioning_state: str | None


NativeConnectionSource = Annotated[
    NativeModelConnectionSourceType
    | VertexEndpointConnectionSourceType
    | FoundryDeploymentConnectionSourceType,
    strawberry.union("NativeConnectionSource"),
]


@strawberry.type(name="NativeModelConnection")
class NativeModelConnectionType:
    family: NativeModelFamily
    source_kind: NativeModelConnectionKind
    configuration_state: NativeModelConfigurationState
    invoke_access: NativeModelInvokeAccess
    reason: str | None
    resource_identity_fingerprint: str | None = strawberry.field(
        description="Hash of the validated recorded resource tuple; not native incarnation, ownership or invocation proof."
    )
    reviewed_source_fingerprint: str | None
    metadata_observed_at: datetime | None
    source: NativeConnectionSource | None
