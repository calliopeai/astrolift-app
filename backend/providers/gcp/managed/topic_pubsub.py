"""Google Cloud Pub/Sub topic and subscription managed-service lifecycle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)

KIND = "topic"
_MUTABLE_TOPIC_FIELDS = {
    "kms_key_name",
    "labels",
    "message_retention_duration",
    "message_storage_policy",
    "message_transforms",
    "schema_settings",
    "ingestion_data_source_settings",
}
_MUTABLE_SUBSCRIPTION_FIELDS = {
    "ack_deadline_seconds",
    "bigtable_config",
    "bigquery_config",
    "cloud_storage_config",
    "enable_exactly_once_delivery",
    "retain_acked_messages",
    "message_retention_duration",
    "labels",
    "message_transforms",
    "expiration_policy",
    "dead_letter_policy",
    "retry_policy",
    "push_config",
}
_IMMUTABLE_SUBSCRIPTION_FIELDS = {
    "filter",
    "enable_message_ordering",
}


class PubSubTopicError(Exception):
    pass


@dataclass(frozen=True)
class PubSubTopicConfig:
    project_id: str
    topic_prefix: str = "astrolift"
    publisher_client: Any | None = None
    subscriber_client: Any | None = None


class PubSubTopicDriver(ManagedServiceDriver):
    def __init__(self, *, config: PubSubTopicConfig) -> None:
        self._config = config
        if config.publisher_client is None or config.subscriber_client is None:
            from google.cloud import pubsub_v1

        self._pub = config.publisher_client or pubsub_v1.PublisherClient()
        self._sub = config.subscriber_client or pubsub_v1.SubscriberClient()

    @driver_op(
        cloud="gcp",
        driver="topic_pubsub",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_pubsub_topic_config"])
        topic_id = self._topic_id(spec)
        topic_path = self._topic_path(topic_id)
        topic = self._topic_document(
            topic_path=topic_path,
            cfg=cfg,
            labels=_labels_for(spec, cfg),
        )
        try:
            try:
                self._pub.create_topic(request=topic)
            except Exception as exc:
                if not _already_exists(exc):
                    raise
            self._reconcile_topic(topic_path, cfg, labels=_labels_for(spec, cfg))
            self._reconcile_subscriptions(topic_id, cfg)
        except Exception as exc:
            return ProvisionResult(
                False,
                "",
                f"provision Pub/Sub topic {topic_id}: {exc}",
                [str(exc)],
            )
        return ProvisionResult(
            True,
            f"{KIND}/{topic_id}",
            f"Pub/Sub topic {topic_id} and declared subscriptions available",
            ready=not bool(cfg.get("subscriptions")),
        )

    @driver_op(cloud="gcp", driver="topic_pubsub")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            topic_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg, partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_pubsub_topic_config"])
        topic_path = self._topic_path(topic_id)
        try:
            self._topic(topic_path)
            self._reconcile_topic(topic_path, cfg, labels=None)
            self._reconcile_subscriptions(topic_id, cfg)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, f"Pub/Sub topic {topic_id} not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update Pub/Sub topic {topic_id}: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Pub/Sub topic {topic_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="topic_pubsub",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        try:
            topic_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        topic_path = self._topic_path(topic_id)
        try:
            topic = self._topic(topic_path)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"Pub/Sub topic {topic_id} already gone")
            return _deprovision_error(spec.handle, "describe Pub/Sub topic", exc)

        subscriptions = self._subscription_paths(topic_path)
        has_retention = bool(_get(topic, "message_retention_duration", ""))
        if (subscriptions or has_retention) and not delete_data:
            details = []
            if subscriptions:
                details.append(f"{len(subscriptions)} subscription(s)")
            if has_retention:
                details.append("topic-retained messages")
            return DeprovisionResult(
                False,
                spec.handle,
                "Pub/Sub topic may retain data through "
                + " and ".join(details)
                + "; set delete_data=true to acknowledge deletion",
                ["retained_messages_require_delete_data"],
                retryable=False,
            )
        if subscriptions and not force_destroy:
            configured = {
                self._subscription_path(topic_id, str(item["name"]))
                for item in list((spec.config or {}).get("subscriptions") or [])
                if isinstance(item, dict) and item.get("name")
            }
            foreign = sorted(set(subscriptions) - configured)
            if foreign:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Pub/Sub topic has subscriptions outside this declaration; "
                    "set force_destroy=true to delete them: " + ", ".join(path.rsplit("/", 1)[-1] for path in foreign),
                    ["foreign_subscriptions_require_force_destroy"],
                    retryable=False,
                )
        for path in subscriptions:
            try:
                self._sub.delete_subscription(request={"subscription": path})
            except Exception as exc:
                if not _not_found(exc):
                    return _deprovision_error(spec.handle, f"delete Pub/Sub subscription {path}", exc)
        try:
            self._pub.delete_topic(request={"topic": topic_path})
        except Exception as exc:
            if not _not_found(exc):
                return _deprovision_error(spec.handle, "delete Pub/Sub topic", exc)
        return DeprovisionResult(
            True,
            spec.handle,
            f"Pub/Sub topic {topic_id} and {len(subscriptions)} subscription(s) deleted",
        )

    @driver_op(cloud="gcp", driver="topic_pubsub")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            topic_id = _parse_handle(handle.handle)
            topic = self._topic(self._topic_path(topic_id))
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", "Pub/Sub topic does not exist")
            return ServiceStatus(handle.handle, "error", f"describe Pub/Sub topic: {exc}")
        topic_state = _state_name(_get(topic, "state", ""))
        if topic_state == "INGESTION_RESOURCE_ERROR":
            return ServiceStatus(handle.handle, "error", "Pub/Sub topic ingestion source is in RESOURCE_ERROR")
        subscriptions = self._subscription_paths(self._topic_path(topic_id))
        owned = [path for path in subscriptions if self._owns_subscription(topic_id, path)]
        for path in owned:
            try:
                subscription = self._sub.get_subscription(request={"subscription": path})
            except Exception as exc:
                if _not_found(exc):
                    return ServiceStatus(
                        handle.handle, "provisioning", f"Pub/Sub subscription {path} is not visible yet"
                    )
                return ServiceStatus(handle.handle, "error", f"describe Pub/Sub subscription {path}: {exc}")
            state = _state_name(_get(subscription, "state", ""))
            if state == "RESOURCE_ERROR":
                detail = _subscription_delivery_state(subscription)
                return ServiceStatus(
                    handle.handle,
                    "error",
                    f"Pub/Sub subscription {path.rsplit('/', 1)[-1]} is in RESOURCE_ERROR{detail}",
                )
            if state not in {"", "0", "STATE_UNSPECIFIED", "1", "ACTIVE"}:
                return ServiceStatus(
                    handle.handle,
                    "provisioning",
                    f"Pub/Sub subscription {path.rsplit('/', 1)[-1]} is {state}",
                )
        encrypted = bool(_get(topic, "kms_key_name", ""))
        return ServiceStatus(
            handle.handle,
            "available",
            f"Pub/Sub topic available ({len(owned)} managed / {len(subscriptions)} attached subscriptions, "
            f"{'CMEK' if encrypted else 'Google-managed encryption'})",
        )

    @driver_op(cloud="gcp", driver="topic_pubsub")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        topic_id = _parse_handle(handle.handle)
        topic_path = self._topic_path(topic_id)
        self._topic(topic_path)
        cfg = dict(config or {})
        access_mode = str(cfg.get("access_mode") or "publish")
        subscription_paths = [
            self._subscription_path(topic_id, str(item["name"]))
            for item in list(cfg.get("subscriptions") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        if access_mode in {"subscribe", "publish_subscribe"} and not subscription_paths:
            raise PubSubTopicError(f"Pub/Sub {access_mode} binding requires at least one declared subscription")
        env_vars = {
            "TOPIC_ARN_OR_ID": ValueRef(literal=topic_path),
            "TOPIC_NAME": ValueRef(literal=topic_id),
            "TOPIC_REGION": ValueRef(literal="global"),
            "PUBSUB_TOPIC": ValueRef(literal=topic_path),
            "PUBSUB_TOPIC_ID": ValueRef(literal=topic_id),
            "PUBSUB_SUBSCRIPTIONS": ValueRef(
                literal=json.dumps(subscription_paths, separators=(",", ":")),
            ),
            "GCP_PROJECT_ID": ValueRef(literal=self._config.project_id),
        }
        if subscription_paths:
            env_vars["PUBSUB_SUBSCRIPTION"] = ValueRef(literal=subscription_paths[0])
        grants: list[Grant] = []
        if access_mode in {"publish", "publish_subscribe", "manage"}:
            grants.append(Grant(topic_path, ["roles/pubsub.publisher"]))
        if access_mode in {"subscribe", "publish_subscribe"}:
            grants.extend(Grant(path, ["roles/pubsub.subscriber"]) for path in subscription_paths)
        if access_mode == "manage":
            grants.append(
                Grant(
                    f"projects/{self._config.project_id}",
                    ["roles/pubsub.editor"],
                ),
            )
        return Binding(
            env_vars=env_vars,
            iam_grants=grants,
            notes=f"Pub/Sub {access_mode} access through GKE Workload Identity.",
        )

    @driver_op(cloud="gcp", driver="topic_pubsub")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "Pub/Sub snapshots capture one subscription backlog and cannot represent a topic with arbitrary "
            "subscriptions in the portable managed-service snapshot contract",
        )

    @driver_op(cloud="gcp", driver="topic_pubsub")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "Pub/Sub topic restore is not portable; seek an explicitly managed subscription to its provider "
            "snapshot before attaching it",
        )

    @driver_op(cloud="gcp", driver="topic_pubsub", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        duration = {"type": "string", "pattern": r"^[0-9]+(?:\.[0-9]+)?s$"}
        string_map = {"type": "object", "additionalProperties": {"type": "string"}}
        message_transform = {
            "type": "object",
            "properties": {
                "disabled": {"type": "boolean"},
                "javascript_udf": {
                    "type": "object",
                    "required": ["function_name", "code"],
                    "properties": {
                        "function_name": {"type": "string"},
                        "code": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "ai_inference": {
                    "type": "object",
                    "required": ["endpoint"],
                    "properties": {
                        "endpoint": {"type": "string"},
                        "unstructured_inference": {
                            "type": "object",
                            "properties": {"parameters": {"type": "object"}},
                            "additionalProperties": False,
                        },
                        "service_account_email": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "oneOf": [
                {"required": ["javascript_udf"]},
                {"required": ["ai_inference"]},
            ],
            "additionalProperties": False,
        }
        ingestion_settings = {
            "type": "object",
            "properties": {
                "platform_logs_settings": {
                    "type": "object",
                    "properties": {
                        "severity": {
                            "type": "string",
                            "enum": ["SEVERITY_UNSPECIFIED", "DISABLED", "DEBUG", "INFO", "WARNING", "ERROR"],
                        },
                    },
                    "additionalProperties": False,
                },
                "aws_kinesis": {
                    "type": "object",
                    "required": ["stream_arn", "consumer_arn", "aws_role_arn", "gcp_service_account"],
                    "properties": {
                        "stream_arn": {"type": "string"},
                        "consumer_arn": {"type": "string"},
                        "aws_role_arn": {"type": "string"},
                        "gcp_service_account": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "cloud_storage": {
                    "type": "object",
                    "required": ["bucket"],
                    "properties": {
                        "bucket": {"type": "string"},
                        "minimum_object_create_time": {"type": "string", "format": "date-time"},
                        "match_glob": {"type": "string"},
                        "text_format": {
                            "type": "object",
                            "properties": {"delimiter": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        "avro_format": {"type": "object", "additionalProperties": False},
                        "pubsub_avro_format": {"type": "object", "additionalProperties": False},
                    },
                    "additionalProperties": False,
                },
                "azure_event_hubs": {
                    "type": "object",
                    "properties": {
                        "resource_group": {"type": "string"},
                        "namespace": {"type": "string"},
                        "event_hub": {"type": "string"},
                        "client_id": {"type": "string"},
                        "tenant_id": {"type": "string"},
                        "subscription_id": {"type": "string"},
                        "gcp_service_account": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "aws_msk": {
                    "type": "object",
                    "required": ["cluster_arn", "topic", "aws_role_arn", "gcp_service_account"],
                    "properties": {
                        "cluster_arn": {"type": "string"},
                        "topic": {"type": "string"},
                        "aws_role_arn": {"type": "string"},
                        "gcp_service_account": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "confluent_cloud": {
                    "type": "object",
                    "required": [
                        "bootstrap_server",
                        "cluster_id",
                        "topic",
                        "identity_pool_id",
                        "gcp_service_account",
                    ],
                    "properties": {
                        "bootstrap_server": {"type": "string"},
                        "cluster_id": {"type": "string"},
                        "topic": {"type": "string"},
                        "identity_pool_id": {"type": "string"},
                        "gcp_service_account": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        }
        subscription = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "ack_deadline_seconds": {"type": "integer", "minimum": 10, "maximum": 600},
                "retain_acked_messages": {"type": "boolean"},
                "message_retention_duration": duration,
                "filter": {"type": "string", "maxLength": 256},
                "enable_message_ordering": {"type": "boolean"},
                "enable_exactly_once_delivery": {"type": "boolean"},
                "labels": string_map,
                "message_transforms": {"type": "array", "items": message_transform},
                "expiration_policy": {
                    "type": "object",
                    "properties": {"ttl": duration},
                    "additionalProperties": False,
                },
                "dead_letter_policy": {
                    "type": "object",
                    "required": ["dead_letter_topic"],
                    "properties": {
                        "dead_letter_topic": {"type": "string"},
                        "max_delivery_attempts": {"type": "integer", "minimum": 5, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
                "retry_policy": {
                    "type": "object",
                    "properties": {
                        "minimum_backoff": duration,
                        "maximum_backoff": duration,
                    },
                    "additionalProperties": False,
                },
                "push_config": {
                    "type": "object",
                    "properties": {
                        "push_endpoint": {"type": "string", "format": "uri"},
                        "attributes": string_map,
                        "oidc_token": {
                            "type": "object",
                            "properties": {
                                "service_account_email": {"type": "string"},
                                "audience": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                        "pubsub_wrapper": {"type": "object", "additionalProperties": False},
                        "no_wrapper": {
                            "type": "object",
                            "properties": {"write_metadata": {"type": "boolean"}},
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                "bigquery_config": {
                    "type": "object",
                    "required": ["table"],
                    "properties": {
                        "table": {"type": "string"},
                        "use_topic_schema": {"type": "boolean"},
                        "use_table_schema": {"type": "boolean"},
                        "write_metadata": {"type": "boolean"},
                        "drop_unknown_fields": {"type": "boolean"},
                        "service_account_email": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "bigtable_config": {
                    "type": "object",
                    "required": ["table"],
                    "properties": {
                        "table": {"type": "string"},
                        "app_profile_id": {"type": "string"},
                        "service_account_email": {"type": "string"},
                        "write_metadata": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "cloud_storage_config": {
                    "type": "object",
                    "required": ["bucket"],
                    "properties": {
                        "bucket": {"type": "string"},
                        "filename_prefix": {"type": "string"},
                        "filename_suffix": {"type": "string"},
                        "filename_datetime_format": {"type": "string"},
                        "text_config": {"type": "object", "additionalProperties": False},
                        "avro_config": {
                            "type": "object",
                            "properties": {
                                "write_metadata": {"type": "boolean"},
                                "use_topic_schema": {"type": "boolean"},
                            },
                            "additionalProperties": False,
                        },
                        "max_duration": duration,
                        "max_bytes": {"type": "integer", "minimum": 1024, "maximum": 10737418240},
                        "max_messages": {"type": "integer", "minimum": 1000},
                        "service_account_email": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "access_mode": {
                    "type": "string",
                    "enum": ["publish", "subscribe", "publish_subscribe", "manage"],
                    "default": "publish",
                },
                "labels": {"type": "object", "additionalProperties": {"type": "string"}},
                "kms_key_name": {"type": "string"},
                "message_retention_duration": duration,
                "message_storage_policy": {
                    "type": "object",
                    "properties": {
                        "allowed_persistence_regions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "uniqueItems": True,
                        },
                        "enforce_in_transit": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "schema_settings": {
                    "type": "object",
                    "required": ["schema"],
                    "properties": {
                        "schema": {"type": "string"},
                        "encoding": {"type": "string", "enum": ["JSON", "BINARY", "ENCODING_UNSPECIFIED"]},
                        "first_revision_id": {"type": "string"},
                        "last_revision_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "ingestion_data_source_settings": ingestion_settings,
                "message_transforms": {"type": "array", "items": message_transform},
                "subscriptions": {"type": "array", "items": subscription},
                "prune_subscriptions": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="topic_pubsub", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "TOPIC_ARN_OR_ID": "Portable provider topic identifier",
                "TOPIC_NAME": "Portable topic name",
                "TOPIC_REGION": "Global Pub/Sub location marker",
                "PUBSUB_TOPIC": "Full Pub/Sub topic resource path",
                "PUBSUB_TOPIC_ID": "Pub/Sub topic id",
                "PUBSUB_SUBSCRIPTIONS": "JSON list of declared subscription resource paths",
                "PUBSUB_SUBSCRIPTION": "First declared subscription path, when present",
                "GCP_PROJECT_ID": "Google Cloud project id",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "ingestion_data_source_settings",
            "kms_key_name",
            "labels",
            "message_retention_duration",
            "message_storage_policy",
            "message_transforms",
            "prune_subscriptions",
            "schema_settings",
            "subscriptions",
        ]

    def _topic(self, topic_path: str) -> Any:
        return self._pub.get_topic(request={"topic": topic_path})

    def _topic_path(self, topic_id: str) -> str:
        return self._pub.topic_path(self._config.project_id, topic_id)

    def _subscription_path(self, topic_id: str, declared_name: str) -> str:
        suffix = _resource_id(declared_name, max_length=200)
        subscription_id = _resource_id(f"{topic_id}-{suffix}", max_length=255)
        return self._sub.subscription_path(self._config.project_id, subscription_id)

    def _topic_id(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            part
            for part in (
                self._config.topic_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "topic",
            )
            if part
        )
        return _resource_id(raw, max_length=255)

    def _topic_document(
        self,
        *,
        topic_path: str,
        cfg: dict[str, Any],
        labels: dict[str, str],
    ) -> dict[str, Any]:
        topic: dict[str, Any] = {"name": topic_path, "labels": labels}
        for field in (
            "kms_key_name",
            "message_retention_duration",
            "message_storage_policy",
            "schema_settings",
            "ingestion_data_source_settings",
            "message_transforms",
        ):
            if field in cfg:
                topic[field] = cfg[field]
        return topic

    def _reconcile_topic(
        self,
        topic_path: str,
        cfg: dict[str, Any],
        *,
        labels: dict[str, str] | None,
    ) -> None:
        paths = sorted(_MUTABLE_TOPIC_FIELDS.intersection(cfg))
        topic: dict[str, Any] = {"name": topic_path}
        if labels is not None:
            topic["labels"] = labels
            if "labels" not in paths:
                paths.append("labels")
        for field in paths:
            if field == "labels" and labels is not None:
                continue
            topic[field] = cfg[field]
        if paths:
            self._pub.update_topic(
                request={
                    "topic": topic,
                    "update_mask": {"paths": sorted(paths)},
                },
            )

    def _subscription_paths(self, topic_path: str) -> list[str]:
        rows = self._pub.list_topic_subscriptions(request={"topic": topic_path})
        return sorted(str(_get(item, "name", item)) for item in rows)

    def _reconcile_subscriptions(self, topic_id: str, cfg: dict[str, Any]) -> None:
        if "subscriptions" not in cfg:
            return
        topic_path = self._topic_path(topic_id)
        desired = list(cfg.get("subscriptions") or [])
        wanted: set[str] = set()
        for declaration in desired:
            path = self._subscription_path(topic_id, str(declaration["name"]))
            wanted.add(path)
            document = self._subscription_document(path, topic_path, declaration)
            try:
                current = self._sub.get_subscription(request={"subscription": path})
            except Exception as exc:
                if not _not_found(exc):
                    raise
                try:
                    self._sub.create_subscription(request=document)
                except Exception as create_exc:
                    if not _already_exists(create_exc):
                        raise
                    current = self._sub.get_subscription(request={"subscription": path})
                else:
                    continue
            for field in _IMMUTABLE_SUBSCRIPTION_FIELDS.intersection(declaration):
                if _normalized(_get(current, field, None)) != _normalized(document.get(field)):
                    raise PubSubTopicError(
                        f"subscription {path.rsplit('/', 1)[-1]} field {field!r} is immutable; "
                        "rename the subscription or restore its existing value",
                    )
            paths = sorted(_MUTABLE_SUBSCRIPTION_FIELDS.intersection(declaration))
            if paths:
                update = {"name": path}
                update.update({field: document.get(field) for field in paths})
                self._sub.update_subscription(
                    request={
                        "subscription": update,
                        "update_mask": {"paths": paths},
                    },
                )
        if cfg.get("prune_subscriptions"):
            for path in self._subscription_paths(topic_path):
                if path not in wanted and self._owns_subscription(topic_id, path):
                    try:
                        self._sub.delete_subscription(request={"subscription": path})
                    except Exception as exc:
                        if not _not_found(exc):
                            raise

    def _owns_subscription(self, topic_id: str, path: str) -> bool:
        prefix = self._sub.subscription_path(self._config.project_id, f"{topic_id}-")
        return path.startswith(prefix)

    def _subscription_document(
        self,
        path: str,
        topic_path: str,
        declaration: dict[str, Any],
    ) -> dict[str, Any]:
        document: dict[str, Any] = {"name": path, "topic": topic_path}
        for field in _MUTABLE_SUBSCRIPTION_FIELDS | _IMMUTABLE_SUBSCRIPTION_FIELDS:
            if field in declaration:
                document[field] = declaration[field]
        return document

    def _validate_config(self, cfg: dict[str, Any], *, partial: bool = False) -> str | None:
        del partial
        access_mode = str(cfg.get("access_mode") or "publish")
        if access_mode not in {"publish", "subscribe", "publish_subscribe", "manage"}:
            return f"access_mode {access_mode!r} is invalid"
        if "message_retention_duration" in cfg:
            error = _duration_error(
                cfg["message_retention_duration"],
                field="message_retention_duration",
                minimum=timedelta(minutes=10),
                maximum=timedelta(days=31),
            )
            if error:
                return error
        ingestion = cfg.get("ingestion_data_source_settings") or {}
        ingestion_sources = [
            field
            for field in ("aws_kinesis", "cloud_storage", "azure_event_hubs", "aws_msk", "confluent_cloud")
            if ingestion.get(field)
        ]
        if len(ingestion_sources) > 1:
            return "ingestion_data_source_settings may configure only one source"
        subscriptions = cfg.get("subscriptions")
        if subscriptions is not None and not isinstance(subscriptions, list):
            return "subscriptions must be an array"
        names: set[str] = set()
        for index, subscription in enumerate(subscriptions or []):
            if not isinstance(subscription, dict):
                return f"subscriptions[{index}] must be an object"
            name = str(subscription.get("name") or "")
            if not name:
                return f"subscriptions[{index}].name is required"
            normalized = _resource_id(name, max_length=200)
            if normalized in names:
                return f"subscriptions[{index}].name collides with another normalized subscription id"
            names.add(normalized)
            delivery_fields = [
                field
                for field in (
                    "push_config",
                    "bigquery_config",
                    "bigtable_config",
                    "cloud_storage_config",
                )
                if subscription.get(field)
            ]
            if len(delivery_fields) > 1:
                return f"subscriptions[{index}] may configure only one delivery destination"
            if subscription.get("enable_exactly_once_delivery") and delivery_fields:
                return f"subscriptions[{index}].enable_exactly_once_delivery is supported only for pull delivery"
            bigquery = subscription.get("bigquery_config") or {}
            if bigquery.get("use_topic_schema") and bigquery.get("use_table_schema"):
                return f"subscriptions[{index}].bigquery_config cannot enable both topic and table schemas"
            if "ack_deadline_seconds" in subscription:
                deadline = subscription["ack_deadline_seconds"]
                if not isinstance(deadline, int) or not 10 <= deadline <= 600:
                    return f"subscriptions[{index}].ack_deadline_seconds must be between 10 and 600"
            if "message_retention_duration" in subscription:
                error = _duration_error(
                    subscription["message_retention_duration"],
                    field=f"subscriptions[{index}].message_retention_duration",
                    minimum=timedelta(minutes=10),
                    maximum=timedelta(days=31),
                )
                if error:
                    return error
            expiration = subscription.get("expiration_policy") or {}
            if "ttl" in expiration:
                error = _duration_error(
                    expiration["ttl"],
                    field=f"subscriptions[{index}].expiration_policy.ttl",
                    minimum=timedelta(days=1),
                    maximum=timedelta(days=36500),
                )
                if error:
                    return error
            retry_policy = subscription.get("retry_policy") or {}
            for field in ("minimum_backoff", "maximum_backoff"):
                if field not in retry_policy:
                    continue
                error = _duration_error(
                    retry_policy[field],
                    field=f"subscriptions[{index}].retry_policy.{field}",
                    minimum=timedelta(seconds=0),
                    maximum=timedelta(seconds=600),
                )
                if error:
                    return error
            storage = subscription.get("cloud_storage_config") or {}
            if "max_duration" in storage:
                error = _duration_error(
                    storage["max_duration"],
                    field=f"subscriptions[{index}].cloud_storage_config.max_duration",
                    minimum=timedelta(minutes=1),
                    maximum=timedelta(minutes=10),
                )
                if error:
                    return error
                if "ack_deadline_seconds" in subscription:
                    seconds = _duration_seconds(storage["max_duration"])
                    if seconds > subscription["ack_deadline_seconds"]:
                        return (
                            f"subscriptions[{index}].cloud_storage_config.max_duration may not exceed "
                            "ack_deadline_seconds"
                        )
            dead_letter = subscription.get("dead_letter_policy") or {}
            attempts = dead_letter.get("max_delivery_attempts")
            if attempts is not None and (not isinstance(attempts, int) or not 5 <= attempts <= 100):
                return f"subscriptions[{index}].dead_letter_policy.max_delivery_attempts must be 5-100"
        return None


def _labels_for(spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, str]:
    labels = {
        "astrolift-managed-by": "platform",
        "astrolift-organization": spec.organization_slug,
        "astrolift-app": spec.app_slug,
        "astrolift-environment": spec.environment_name,
    }
    if spec.binding_id:
        labels["astrolift-binding"] = spec.binding_id
    if spec.managed_service_id:
        labels["astrolift-managed-service-id"] = spec.managed_service_id
    for key, value in dict(cfg.get("labels") or {}).items():
        labels[_label(key)] = _label(value)
    for key, value in (spec.tags or {}).items():
        labels[_label(f"extra-{key}")] = _label(value)
    return {key[:63]: value[:63] for key, value in labels.items() if value}


def _label(value: Any) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", str(value).lower()).strip("-_") or "value"


def _resource_id(value: str, *, max_length: int) -> str:
    clean = re.sub(r"[^A-Za-z0-9._~+%-]+", "-", value).strip("-._")
    if not clean:
        raise ValueError("Pub/Sub resource id cannot be empty after normalization")
    if clean.lower().startswith("goog"):
        clean = f"astrolift-{clean}"
    if not clean[0].isalpha():
        clean = f"a-{clean}"
    return clean[:max_length].rstrip("-._")


def _parse_handle(handle: str) -> str:
    kind, separator, topic_id = handle.partition("/")
    if separator != "/" or kind != KIND or not topic_id or "/" in topic_id:
        raise ValueError(f"invalid Pub/Sub topic handle {handle!r}; expected 'topic/<topic-id>'")
    return topic_id


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _normalized(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _state_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if name:
        return str(name).upper()
    text = str(value).upper()
    return text.rsplit(".", 1)[-1]


def _subscription_delivery_state(subscription: Any) -> str:
    for field in ("bigquery_config", "bigtable_config", "cloud_storage_config"):
        config = _get(subscription, field, None)
        if config:
            state = _state_name(_get(config, "state", ""))
            if state:
                return f" ({field}: {state})"
    return ""


def _not_found(exc: Exception) -> bool:
    return type(exc).__name__ in {"NotFound", "ResourceNotFoundError"} or "not found" in str(exc).lower()


def _already_exists(exc: Exception) -> bool:
    return type(exc).__name__ in {"AlreadyExists", "Conflict"} or "already exists" in str(exc).lower()


def _duration_error(
    value: Any,
    *,
    field: str,
    minimum: timedelta,
    maximum: timedelta,
) -> str | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", str(value))
    if match is None:
        return f"{field} must be a Google Duration such as '86400s'"
    duration = timedelta(seconds=float(match.group(1)))
    if not minimum <= duration <= maximum:
        return f"{field} must be between {int(minimum.total_seconds())}s and {int(maximum.total_seconds())}s"
    return None


def _duration_seconds(value: Any) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", str(value))
    if match is None:
        raise ValueError(f"invalid Google Duration {value!r}")
    return float(match.group(1))


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [str(exc)])


__all__ = ["PubSubTopicConfig", "PubSubTopicDriver", "PubSubTopicError"]
