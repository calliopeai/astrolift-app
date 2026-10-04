"""Existing Bedrock connections: exact metadata and workload identity, never allocation."""

import hashlib
import json
import re
from dataclasses import asdict

from django.utils import timezone


class NativeConnectionUnavailable(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def is_bedrock_connection(service):
    config = service.config if isinstance(service.config, dict) else {}
    return (
        service.kind == "model_endpoint"
        and service.variant == "bedrock"
        and service.organization_id is not None
        and config.get("existing_connection_only") is True
        and config.get("model_source") == "bedrock"
    )


def enabled():
    from constance import config

    return config.BEDROCK_MODEL_CONNECTIONS_ENABLED is True


def catalogue_config(cluster, *, require_enabled=True):
    from aws.bedrock_catalogue import BedrockCatalogueConfig

    from core.cluster_credentials import credential_for_cluster

    provider = cluster.provider_plugin
    if provider.slug != "aws" or (require_enabled and (not enabled() or not provider.is_enabled)):
        raise NativeConnectionUnavailable("Bedrock model connections are not enabled.")
    pc = cluster.provider_config if isinstance(cluster.provider_config, dict) else {}
    ac = cluster.auth_config if isinstance(cluster.auth_config, dict) else {}
    region = pc.get("region", ac.get("region", cluster.region))
    credential = credential_for_cluster(cluster)
    if not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
        raise NativeConnectionUnavailable("Bedrock region is unavailable.")
    if not re.fullmatch(r"\d{12}", credential.declared_account):
        raise NativeConnectionUnavailable("An exact AWS account declaration is required.")
    return BedrockCatalogueConfig(region=region, credential=credential)


def declaration(cluster, *, require_enabled=True):
    config = catalogue_config(cluster, require_enabled=require_enabled)
    return {
        "cluster_id": str(cluster.guid),
        "provider_id": str(cluster.provider_plugin.guid),
        "account_id": config.credential.declared_account,
        "region": config.region,
        "credential_sha256": fingerprint(asdict(config.credential)),
    }


def catalogue(cluster):
    from aws.bedrock_catalogue import BedrockCatalogue

    return BedrockCatalogue(catalogue_config(cluster))


def source_identity(detail):
    from aws.bedrock_catalogue import BedrockSourceKind, CatalogueState

    source, identity = detail.source, detail.identity
    if detail.state != CatalogueState.METADATA or source is None or identity is None:
        raise NativeConnectionUnavailable("The exact Bedrock source could not be verified.")
    if source.kind == BedrockSourceKind.FOUNDATION_MODEL:
        if "ON_DEMAND" not in source.inference_types or source.lifecycle != "ACTIVE":
            raise NativeConnectionUnavailable("This foundation model is not an active on-demand source.")
    elif source.profile_type not in ("SYSTEM_DEFINED", "APPLICATION") or source.lifecycle != "ACTIVE":
        raise NativeConnectionUnavailable("This inference profile is not active.")
    return {
        "protocol": "bedrock",
        "source_kind": source.kind.value,
        "source_id": source.identifier,
        "source_arn": source.arn,
        "destination_model_arns": sorted(source.destination_model_arns),
        "account_id": identity.account_id,
        "region": identity.region,
        "partition": identity.partition,
    }


def connection_config(cluster, organization, detail):
    source = source_identity(detail)
    target = declaration(cluster)
    if (source["account_id"], source["region"]) != (target["account_id"], target["region"]):
        raise NativeConnectionUnavailable("Bedrock source and placement identity differ.")
    native = source | target | {"organization_id": str(organization.guid), "schema": 1}
    native["source_fingerprint"] = fingerprint(source)
    return {
        "model_source": "bedrock",
        "existing_connection_only": True,
        "native_connection": native,
        "native_metadata_observed_at": timezone.now().isoformat(),
        "allow_subscriptions": True,
        "sharing_mode": "shared",
    }


def native_identity(service, *, cleanup=False):
    if not is_bedrock_connection(service):
        raise NativeConnectionUnavailable("This is not an existing Bedrock model connection.")
    value = (service.config or {}).get("native_connection")
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise NativeConnectionUnavailable("Bedrock connection identity is unavailable.")
    target = declaration(service.tenant_cluster, require_enabled=not cleanup)
    if any(value.get(key) != expected for key, expected in target.items()):
        raise NativeConnectionUnavailable("Bedrock placement or credential declaration changed.")
    if value.get("organization_id") != str(service.organization.guid):
        raise NativeConnectionUnavailable("Bedrock connection owner changed.")
    from aws.bedrock_catalogue import BedrockCatalogue, BedrockSourceKind

    try:
        kind = BedrockSourceKind(value["source_kind"])
        with BedrockCatalogue(
            catalogue_config(service.tenant_cluster, require_enabled=not cleanup)
        ) as validator:
            validator._selector(kind, value["source_arn"])
        if not isinstance(value["source_id"], str) or not isinstance(value["destination_model_arns"], list):
            raise ValueError
        if (
            value["source_id"] != value["source_arn"].rsplit("/", 1)[-1]
            or len(value["destination_model_arns"]) > 16
            or len(set(value["destination_model_arns"])) != len(value["destination_model_arns"])
            or any(
                not isinstance(arn, str)
                or not re.fullmatch(
                    rf"arn:{re.escape(value['partition'])}:bedrock:[a-z0-9-]+::foundation-model/[a-zA-Z0-9.:-]+",
                    arn,
                )
                for arn in value["destination_model_arns"]
            )
        ):
            raise ValueError
        source = {
            key: value[key]
            for key in (
                "protocol",
                "source_kind",
                "source_id",
                "source_arn",
                "destination_model_arns",
                "account_id",
                "region",
                "partition",
            )
        }
        if source["protocol"] != "bedrock" or fingerprint(source) != value["source_fingerprint"]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise NativeConnectionUnavailable("Bedrock connection identity is invalid.") from None
    return value


def current(service):
    try:
        native_identity(service)
        return True
    except (TypeError, ValueError):
        return False


def canonical_handle(service):
    native = native_identity(service)
    return f"model_endpoint/bedrock-connection/{service.guid}/{native['source_fingerprint']}"


def connectable(service):
    try:
        return (
            current(service)
            and service.status == "active"
            and service.backend_ref == canonical_handle(service)
            and service.applied_config == service.config
            and service.applied_subscription_revision == service.subscription_revision
        )
    except (TypeError, ValueError):
        return False


def verify_source(service, *, checkpoint=None):
    from aws.bedrock_catalogue import BedrockSourceKind

    native = native_identity(service)
    if checkpoint:
        checkpoint()
    with catalogue(service.tenant_cluster) as reader:
        detail = reader.detail(BedrockSourceKind(native["source_kind"]), native["source_arn"])
    observed = source_identity(detail)
    if observed != {key: native[key] for key in observed}:
        raise NativeConnectionUnavailable(
            "Bedrock source destinations changed. Register a reviewed replacement."
        )
    native_identity(service)
    if checkpoint:
        checkpoint()
    return detail


def binding(service):
    from _sdk.managed_service import Binding, Grant, ValueRef

    native = native_identity(service)
    if service.backend_ref != canonical_handle(service):
        raise NativeConnectionUnavailable("Bedrock recorded connection identity changed.")
    from botocore.session import get_session

    endpoint = (
        get_session()
        .get_component("endpoint_resolver")
        .construct_endpoint("bedrock-runtime", native["region"])
    )
    if not endpoint or not isinstance(endpoint.get("hostname"), str):
        raise NativeConnectionUnavailable("Bedrock runtime endpoint is unavailable.")
    resources = [native["source_arn"], *native["destination_model_arns"]]
    return Binding(
        env_vars={
            "MODEL_ENDPOINT_URL": ValueRef(literal=f"https://{endpoint['hostname']}"),
            "MODEL_DEPLOYMENT_NAME": ValueRef(literal=native["source_arn"]),
            "MODEL_REGION": ValueRef(literal=native["region"]),
            "MODEL_API_STYLE": ValueRef(literal="bedrock"),
            "MODEL_AUTH_MODE": ValueRef(literal="cloud_identity"),
        },
        iam_grants=[
            Grant(resource=resource, actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"])
            for resource in dict.fromkeys(resources)
        ],
        notes="Configured Bedrock connection only; invocation and external IAM access are not verified.",
    )


def model_connectable(service):
    if is_bedrock_connection(service):
        return connectable(service)
    from astrolift_services.schema.model_types import cluster_model_to_type

    return cluster_model_to_type(service, dedicated=None).ready is True
