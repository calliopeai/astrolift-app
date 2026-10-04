"""Safe common metadata projection, without cloud reads or adoption effects."""

from astrolift_services.schema.native_model_types import (
    NativeModelConfigurationState,
    NativeModelConnectionKind,
    NativeModelConnectionType,
    NativeModelFamily,
    NativeModelInvokeAccess,
)

_VARIANTS = {
    "bedrock": NativeModelFamily.BEDROCK,
    "vertex_ai": NativeModelFamily.VERTEX,
    "azure_foundry": NativeModelFamily.FOUNDRY,
}


def native_family(service):
    if service.kind != "model_endpoint":
        return None
    family = _VARIANTS.get(service.variant)
    if family is not None:
        return family
    config = service.config if isinstance(service.config, dict) else {}
    if service.variant != "vllm" or (
        config.get("model_source") not in (None, "huggingface", "local_artifact")
        or config.get("existing_connection_only") is True
        or "native_connection" in config
    ):
        return NativeModelFamily.UNKNOWN
    return None


def native_connection_to_type(service):
    family = native_family(service)
    if family is None:
        return None
    config = service.config if isinstance(service.config, dict) else {}
    stored = config.get("native_connection")
    stored_kind = stored.get("source_kind") if isinstance(stored, dict) else None
    kind = (
        NativeModelConnectionKind.BEDROCK_FOUNDATION_MODEL
        if family == NativeModelFamily.BEDROCK and stored_kind == "foundation_model"
        else NativeModelConnectionKind.BEDROCK_INFERENCE_PROFILE
        if family == NativeModelFamily.BEDROCK and stored_kind == "inference_profile"
        else NativeModelConnectionKind.VERTEX_ENDPOINT
        if family == NativeModelFamily.VERTEX
        else NativeModelConnectionKind.FOUNDRY_DEPLOYMENT
        if family == NativeModelFamily.FOUNDRY
        else NativeModelConnectionKind.UNKNOWN
    )
    source = resource_hash = reviewed_hash = observed_at = None
    reason = "Native connection configuration is unavailable."
    if family == NativeModelFamily.BEDROCK:
        from astrolift_services.native_model_connections import fingerprint
        from astrolift_services.schema.bedrock_model_connections import native_source_to_type

        try:
            source = native_source_to_type(service)
            if source is not None:
                resource_hash = fingerprint(
                    {
                        "protocol": source.protocol.value,
                        "source_kind": source.source_kind.value,
                        "account_id": source.account_id,
                        "region": source.region,
                        "partition": source.partition,
                        "source_id": source.source_id,
                        "source_arn": source.source_arn,
                    }
                )
                reviewed_hash = source.source_fingerprint
                observed_at = source.metadata_observed_at
                reason = None
        except (KeyError, TypeError, ValueError):
            source = resource_hash = reviewed_hash = observed_at = None
    elif family in (NativeModelFamily.VERTEX, NativeModelFamily.FOUNDRY):
        reason = "Native connection adoption is not implemented for this family."
    else:
        reason = "Native connection source family is unsupported or inconsistent."
    return NativeModelConnectionType(
        family=family,
        source_kind=kind,
        configuration_state=NativeModelConfigurationState.CONFIGURED
        if source is not None
        else NativeModelConfigurationState.UNAVAILABLE,
        invoke_access=NativeModelInvokeAccess.UNKNOWN,
        reason=reason,
        resource_identity_fingerprint=resource_hash,
        reviewed_source_fingerprint=reviewed_hash,
        metadata_observed_at=observed_at,
        source=source,
    )
