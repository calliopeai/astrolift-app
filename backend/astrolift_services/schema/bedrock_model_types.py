"""Safe typed native model identities; no credential material."""

from datetime import datetime
from enum import Enum

import strawberry


@strawberry.enum
class BedrockModelSourceKind(Enum):
    FOUNDATION_MODEL = "foundation_model"
    INFERENCE_PROFILE = "inference_profile"


@strawberry.enum
class NativeModelProtocol(Enum):
    BEDROCK = "bedrock"


@strawberry.type(name="NativeModelConnectionSource")
class NativeModelConnectionSourceType:
    protocol: NativeModelProtocol
    source_kind: BedrockModelSourceKind
    account_id: str
    region: str
    partition: str
    source_id: str
    source_arn: str
    destination_model_arns: list[str]
    source_fingerprint: str
    metadata_observed_at: datetime | None
    configuration_state: str
    invoke_access: str = "unknown"


@strawberry.type(name="BedrockModelSource")
class BedrockModelSourceType:
    identity: NativeModelConnectionSourceType
    name: str
    provider: str | None
    input_modalities: list[str]
    output_modalities: list[str]
    streaming: bool | None
    lifecycle: str | None
    profile_type: str | None
    registerable: bool
    reason: str | None


@strawberry.type(name="BedrockModelSources")
class BedrockModelSourcesType:
    items: list[BedrockModelSourceType]
    state: str
    reason: str | None
    truncated: bool
    partial: bool


@strawberry.type(name="BedrockModelConnectionAction")
class BedrockModelConnectionActionType:
    enabled: bool
    allowed: bool
    reason: str | None
