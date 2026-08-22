"""AWS CloudWatch project observability-bundle lifecycle driver."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from _sdk._telemetry import driver_op
from _sdk.cloud_credentials import CredentialedConfig
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
from aws.managed._base import ManagedServiceError, handle_for, parse_handle, tags_for
from aws.session import aws_client

KIND = "observability"
_BUNDLE_TAG = "astrolift.io/observability-bundle"
_LOG_GROUP_RE = re.compile(r"^[.\-_/#A-Za-z0-9]{1,512}$")
_DASHBOARD_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_RETENTION_DAYS = {
    1,
    3,
    5,
    7,
    14,
    30,
    60,
    90,
    120,
    150,
    180,
    365,
    400,
    545,
    731,
    1096,
    1827,
    2192,
    2557,
    2922,
    3288,
    3653,
}
_RESERVED_LOG_CREATE = {"deletionProtectionEnabled", "kmsKeyId", "logGroupClass", "logGroupName", "tags"}
_RESERVED_NAMED_REQUESTS = {
    "metric_alarms": {"AlarmName", "Tags"},
    "composite_alarms": {"AlarmName", "Tags"},
    "insight_rules": {"RuleName", "Tags"},
    "metric_streams": {"Name", "Tags"},
    "metric_filters": {"filterName", "logGroupName"},
    "subscription_filters": {"filterName", "logGroupName"},
}
_REQUIRED_NAMED_REQUESTS = {
    "metric_alarms": set(),
    "composite_alarms": {"AlarmRule"},
    "insight_rules": {"RuleDefinition"},
    "metric_streams": {"FirehoseArn", "RoleArn", "OutputFormat"},
    "metric_filters": {"filterPattern", "metricTransformations"},
    "subscription_filters": {"filterPattern", "destinationArn"},
}


@dataclass(frozen=True)
class CloudWatchConfig(CredentialedConfig):
    region: str
    account_id: str = ""
    log_group_prefix: str = "/astrolift"
    retention_days_default: int = 30
    log_group_class_default: str = "STANDARD"
    deletion_protection_default: bool = True
    dashboard_enabled_default: bool = True


class CloudWatchDriver(ManagedServiceDriver):
    """Own a coherent CloudWatch Logs, alarms, dashboard, and metrics bundle."""

    def __init__(
        self,
        *,
        config: CloudWatchConfig,
        logs_client: Any | None = None,
        cloudwatch_client: Any | None = None,
    ) -> None:
        self._config = config
        if logs_client is None:
            logs_client = aws_client("logs", region=config.region, credential=config.credential)
        if cloudwatch_client is None:
            cloudwatch_client = aws_client("cloudwatch", region=config.region, credential=config.credential)
        self._logs = logs_client
        self._cw = cloudwatch_client

    @driver_op(
        cloud="aws",
        driver="cloudwatch",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_cloudwatch_config"])
        bundle_id = _bundle_id(spec)
        handle = handle_for(kind=KIND, resource_id=bundle_id)
        try:
            self._reconcile(bundle_id, cfg, spec=spec)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision CloudWatch bundle: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"CloudWatch bundle {bundle_id} reconciled", ready=True)

    @driver_op(cloud="aws", driver="cloudwatch")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, bundle_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_cloudwatch_config"])
        try:
            self._reconcile(bundle_id, cfg)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update CloudWatch bundle: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"CloudWatch bundle {bundle_id} reconciled")

    @driver_op(
        cloud="aws",
        driver="cloudwatch",
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
        _, bundle_id = parse_handle(spec.handle)
        protected = bool(
            spec.config.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"CloudWatch bundle {bundle_id} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            groups = self._owned_log_groups(bundle_id)
            if delete_data and not force_destroy:
                cloud_protected = [
                    str(group.get("logGroupName") or "") for group in groups if group.get("deletionProtectionEnabled")
                ]
                if cloud_protected:
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        "CloudWatch log group deletion protection is enabled: " + ", ".join(cloud_protected),
                        ["cloud_deletion_protection_enabled"],
                        retryable=False,
                    )
            self._delete_cloudwatch_resources(bundle_id)
            if delete_data:
                for group in groups:
                    name = str(group["logGroupName"])
                    if group.get("deletionProtectionEnabled") and force_destroy:
                        self._logs.put_log_group_deletion_protection(
                            logGroupIdentifier=name,
                            deletionProtectionEnabled=False,
                        )
                    self._logs.delete_log_group(logGroupName=name)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"CloudWatch bundle {bundle_id} already gone")
            return DeprovisionResult(False, spec.handle, f"delete CloudWatch bundle: {exc}", [str(exc)])
        suffix = " and retained its log groups" if groups and not delete_data else ""
        return DeprovisionResult(True, spec.handle, f"CloudWatch bundle {bundle_id} deleted{suffix}")

    @driver_op(cloud="aws", driver="cloudwatch")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, bundle_id = parse_handle(handle.handle)
        try:
            groups = self._owned_log_groups(bundle_id)
            alarms = self._owned_alarms(bundle_id)
            dashboards = self._owned_dashboards(bundle_id)
            insights = self._owned_insight_rules(bundle_id)
            streams = self._owned_metric_streams(bundle_id)
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe CloudWatch bundle: {exc}")
        count = len(groups) + len(alarms) + len(dashboards) + len(insights) + len(streams)
        if not count:
            return ServiceStatus(handle.handle, "deprovisioned", f"CloudWatch bundle {bundle_id} has no resources")
        return ServiceStatus(
            handle.handle,
            "available",
            f"CloudWatch bundle has {len(groups)} log groups, {len(alarms)} alarms, "
            f"{len(dashboards)} dashboards, {len(insights)} insight rules, and {len(streams)} metric streams",
        )

    @driver_op(cloud="aws", driver="cloudwatch")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, bundle_id = parse_handle(handle.handle)
        cfg = config or {}
        group = next(iter(self._owned_log_groups(bundle_id)), None)
        group_name = str((group or {}).get("logGroupName") or "")
        group_arn = _log_group_arn(group or {})
        log_class = str((group or {}).get("logGroupClass") or "STANDARD")
        dashboard = next(iter(self._owned_dashboards(bundle_id)), None)
        dashboard_name = str((dashboard or {}).get("DashboardName") or "")
        dashboard_url = _dashboard_url(self._config.region, dashboard_name)
        return Binding(
            env_vars={
                "OBSERVABILITY_PROVIDER": ValueRef(literal="cloudwatch"),
                "LOG_GROUP": ValueRef(literal=group_name),
                "METRICS_ENDPOINT": ValueRef(
                    literal=f"https://monitoring.{self._config.region}.amazonaws.com",
                ),
                "DASHBOARD_URL": ValueRef(literal=dashboard_url),
                "CLOUDWATCH_LOG_GROUP": ValueRef(literal=group_name),
                "CLOUDWATCH_LOG_GROUP_ARN": ValueRef(literal=group_arn),
                "CLOUDWATCH_LOGS_ENDPOINT": ValueRef(
                    literal=f"https://logs.{self._config.region}.amazonaws.com",
                ),
                "CLOUDWATCH_DASHBOARD_NAME": ValueRef(literal=dashboard_name),
                "CLOUDWATCH_BUNDLE_ID": ValueRef(literal=bundle_id),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=_binding_grants(group_arn, cfg, log_class=log_class),
            notes="CloudWatch Logs and metrics bundle. Log data is retained unless delete_data=true.",
        )

    @driver_op(cloud="aws", driver="cloudwatch")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "CloudWatch log events cannot be snapshotted; configuration is reconstructed from the manifest",
        )

    @driver_op(cloud="aws", driver="cloudwatch")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "CloudWatch bundle restore is not supported; reprovision it from configuration",
            ["not_implemented"],
        )

    @driver_op(cloud="aws", driver="cloudwatch", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        declaration = {
            "type": "object",
            "required": ["name", "request"],
            "properties": {"name": {"type": "string"}, "request": {"type": "object"}},
        }
        return {
            "type": "object",
            "properties": {
                "log_group": {
                    "oneOf": [
                        {"type": "boolean"},
                        {
                            "type": "object",
                            "properties": {
                                "enabled": {"type": "boolean", "default": True},
                                "name": {"type": "string"},
                                "class": {
                                    "type": "string",
                                    "enum": ["STANDARD", "INFREQUENT_ACCESS", "DELIVERY"],
                                },
                                "retention_days": {"type": "integer"},
                                "kms_key_id": {"type": "string"},
                                "deletion_protection": {"type": "boolean"},
                                "create": {"type": "object"},
                                "data_protection_policy": {},
                                "index_policy": {},
                                "transformer": {"type": "array", "items": {"type": "object"}},
                                "metric_filters": {"type": "array", "items": declaration},
                                "subscription_filters": {"type": "array", "items": declaration},
                            },
                        },
                    ],
                },
                "metric_alarms": {"type": "array", "items": declaration},
                "composite_alarms": {"type": "array", "items": declaration},
                "dashboard": {
                    "oneOf": [
                        {"type": "boolean"},
                        {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "body": {},
                                "allow_validation_messages": {"type": "boolean", "default": False},
                            },
                        },
                    ],
                },
                "insight_rules": {"type": "array", "items": declaration},
                "metric_streams": {"type": "array", "items": declaration},
                "access_mode": {
                    "type": "string",
                    "enum": ["write", "read", "both", "logs", "metrics", "none"],
                    "default": "write",
                },
                "deletion_protection": {"type": "boolean", "default": True},
            },
        }

    @driver_op(cloud="aws", driver="cloudwatch", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "OBSERVABILITY_PROVIDER": "Portable provider identifier",
                "LOG_GROUP": "Portable log group name",
                "METRICS_ENDPOINT": "Portable metrics API endpoint",
                "DASHBOARD_URL": "Portable dashboard URL",
                "CLOUDWATCH_LOG_GROUP": "CloudWatch Logs group name",
                "CLOUDWATCH_LOG_GROUP_ARN": "CloudWatch Logs group ARN",
                "CLOUDWATCH_LOGS_ENDPOINT": "CloudWatch Logs API endpoint",
                "CLOUDWATCH_DASHBOARD_NAME": "CloudWatch dashboard name",
                "CLOUDWATCH_BUNDLE_ID": "Astrolift observability bundle identity",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return ["*"]

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        log_cfg = _log_config(cfg, self._config)
        if not isinstance(log_cfg, dict):
            return "config.log_group must be a boolean or object"
        if "enabled" in log_cfg and not isinstance(log_cfg["enabled"], bool):
            return "log_group.enabled must be a boolean"
        if not log_cfg.get("enabled", True) and set(log_cfg) - {"enabled"}:
            return "disabled config.log_group cannot declare log-group settings"
        for collection in (
            "metric_alarms",
            "composite_alarms",
            "insight_rules",
            "metric_streams",
        ):
            error = _validate_declarations(collection, cfg.get(collection) or [])
            if error:
                return error
        if len(log_cfg.get("subscription_filters") or []) > 2:
            return "CloudWatch Logs supports at most two subscription filters per log group"
        for collection in ("metric_filters", "subscription_filters"):
            error = _validate_declarations(collection, log_cfg.get(collection) or [])
            if error:
                return error
        if log_cfg.get("enabled", True):
            if "name" in log_cfg and not isinstance(log_cfg["name"], str):
                return "log_group.name must be a string"
            name = str(log_cfg.get("name") or self._default_log_group("validation"))
            if not _LOG_GROUP_RE.fullmatch(name) or name.startswith("aws/"):
                return "log_group.name is invalid or uses the reserved aws/ prefix"
            log_class = str(log_cfg.get("class") or self._config.log_group_class_default)
            if log_class not in {"STANDARD", "INFREQUENT_ACCESS", "DELIVERY"}:
                return "log_group.class must be STANDARD, INFREQUENT_ACCESS, or DELIVERY"
            retention = log_cfg.get("retention_days", self._config.retention_days_default)
            if retention is not None:
                if isinstance(retention, bool):
                    return "log_group.retention_days must be an integer"
                try:
                    retention_value = int(retention)
                except (TypeError, ValueError):
                    return "log_group.retention_days must be an integer"
                if retention_value not in _RETENTION_DAYS:
                    return "log_group.retention_days is not supported by CloudWatch Logs"
                if log_class == "DELIVERY" and retention_value != 1:
                    return "DELIVERY log groups retain events for exactly one day"
            standard_only = {
                "index_policy": log_cfg.get("index_policy") is not None,
                "transformer": bool(log_cfg.get("transformer")),
                "metric_filters": bool(log_cfg.get("metric_filters")),
                "subscription_filters": bool(log_cfg.get("subscription_filters")),
            }
            unsupported = sorted(name for name, present in standard_only.items() if present)
            if log_class != "STANDARD" and unsupported:
                return f"log_group.{', '.join(unsupported)} require the STANDARD log class"
            if log_class == "DELIVERY" and log_cfg.get("data_protection_policy") is not None:
                return "log_group.data_protection_policy is not supported by the DELIVERY log class"
            if "deletion_protection" in log_cfg and not isinstance(log_cfg["deletion_protection"], bool):
                return "log_group.deletion_protection must be a boolean"
            if "kms_key_id" in log_cfg and not isinstance(log_cfg["kms_key_id"], str):
                return "log_group.kms_key_id must be a string"
            transformer = log_cfg.get("transformer")
            if transformer is not None and (
                not isinstance(transformer, list)
                or not 1 <= len(transformer) <= 20
                or not all(isinstance(item, dict) for item in transformer)
            ):
                return "log_group.transformer must contain 1-20 processor objects or be null"
            create = log_cfg.get("create") or {}
            if not isinstance(create, dict):
                return "log_group.create must be an object"
            reserved = _RESERVED_LOG_CREATE.intersection(create)
            if reserved:
                return "log_group.create cannot override Astrolift-owned fields: " + ", ".join(sorted(reserved))
            for policy_name in ("data_protection_policy", "index_policy"):
                if log_cfg.get(policy_name) is not None:
                    try:
                        parsed = json.loads(_json_document(log_cfg[policy_name]))
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        return f"log_group.{policy_name} must be valid JSON: {exc}"
                    if not isinstance(parsed, dict):
                        return f"log_group.{policy_name} must encode an object"
        dashboard = cfg.get("dashboard", self._config.dashboard_enabled_default)
        if not isinstance(dashboard, (bool, dict)):
            return "config.dashboard must be a boolean or object"
        if isinstance(dashboard, dict) and dashboard.get("body") is not None:
            try:
                body = json.loads(_json_document(dashboard["body"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return f"dashboard.body must be valid JSON: {exc}"
            if not isinstance(body, dict):
                return "dashboard.body must encode an object"
        if dashboard is not False:
            dashboard_cfg = dashboard if isinstance(dashboard, dict) else {}
            if "name" in dashboard_cfg and not isinstance(dashboard_cfg["name"], str):
                return "dashboard.name must be a string"
            if "allow_validation_messages" in dashboard_cfg and not isinstance(
                dashboard_cfg["allow_validation_messages"],
                bool,
            ):
                return "dashboard.allow_validation_messages must be a boolean"
            dashboard_name = str(dashboard_cfg.get("name") or "astrolift-validation")
            if not _DASHBOARD_RE.fullmatch(dashboard_name):
                return "dashboard.name must be 1-255 letters, digits, hyphens, or underscores"
        access_mode = str(cfg.get("access_mode") or "write")
        if access_mode not in {"write", "read", "both", "logs", "metrics", "none"}:
            return "access_mode must be write, read, both, logs, metrics, or none"
        if "deletion_protection" in cfg and not isinstance(cfg["deletion_protection"], bool):
            return "deletion_protection must be a boolean"
        if (
            not log_cfg.get("enabled", True)
            and dashboard is False
            and not any(
                cfg.get(name) for name in ("metric_alarms", "composite_alarms", "insight_rules", "metric_streams")
            )
        ):
            return "CloudWatch bundle must declare at least one managed resource"
        return ""

    def _reconcile(
        self,
        bundle_id: str,
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None = None,
    ) -> None:
        tags = _bundle_tags(bundle_id, spec)
        log_cfg = _log_config(cfg, self._config)
        desired_log_group = ""
        if log_cfg.get("enabled", True):
            desired_log_group = str(log_cfg.get("name") or self._default_log_group(bundle_id))
            self._reconcile_log_group(desired_log_group, log_cfg, tags, bundle_id=bundle_id)
        for group in self._owned_log_groups(bundle_id):
            if str(group.get("logGroupName") or "") != desired_log_group:
                raise ManagedServiceError(
                    "changing or disabling a managed log group requires deprovisioning the bundle; "
                    "log data is never pruned during update",
                )
        desired_alarms = self._reconcile_alarms(bundle_id, cfg, tags)
        desired_dashboards = self._reconcile_dashboards(
            bundle_id,
            cfg,
            tags,
            desired_log_group,
            log_class=str(log_cfg.get("class") or self._config.log_group_class_default),
        )
        desired_insights = self._reconcile_insights(bundle_id, cfg, tags)
        desired_streams = self._reconcile_streams(bundle_id, cfg, tags)
        self._prune_cloudwatch_resources(
            bundle_id,
            alarms=desired_alarms,
            dashboards=desired_dashboards,
            insights=desired_insights,
            streams=desired_streams,
        )

    def _reconcile_log_group(
        self,
        name: str,
        cfg: dict[str, Any],
        tags: dict[str, str],
        *,
        bundle_id: str,
    ) -> None:
        existing = self._log_group(name)
        log_class = str(cfg.get("class") or self._config.log_group_class_default)
        protected = bool(cfg.get("deletion_protection", self._config.deletion_protection_default))
        if existing is None:
            request = dict(cfg.get("create") or {})
            request.update(
                logGroupName=name,
                logGroupClass=log_class,
                deletionProtectionEnabled=protected,
                tags=tags,
            )
            if cfg.get("kms_key_id"):
                request["kmsKeyId"] = str(cfg["kms_key_id"])
            self._logs.create_log_group(**request)
            existing = self._log_group(name) or {
                "logGroupName": name,
                "logGroupClass": log_class,
                "deletionProtectionEnabled": protected,
            }
        else:
            self._assert_log_group_owned(existing, name, bundle_id)
            if str(existing.get("logGroupClass") or "STANDARD") != log_class:
                raise ManagedServiceError("CloudWatch log group class is immutable; reprovision is required")
            self._logs.tag_resource(resourceArn=_log_group_arn(existing), tags=tags)
            if bool(existing.get("deletionProtectionEnabled")) != protected:
                self._logs.put_log_group_deletion_protection(
                    logGroupIdentifier=name,
                    deletionProtectionEnabled=protected,
                )
            current_kms = str(existing.get("kmsKeyId") or "")
            desired_kms = str(cfg.get("kms_key_id") or "")
            if desired_kms and desired_kms != current_kms:
                self._logs.associate_kms_key(logGroupName=name, kmsKeyId=desired_kms)
            elif current_kms and not desired_kms:
                self._logs.disassociate_kms_key(logGroupName=name)
        retention = cfg.get("retention_days", self._config.retention_days_default)
        if retention is None:
            self._logs.delete_retention_policy(logGroupName=name)
        else:
            self._logs.put_retention_policy(logGroupName=name, retentionInDays=int(retention))
        self._reconcile_log_policy(name, cfg, "data_protection_policy")
        self._reconcile_log_policy(name, cfg, "index_policy")
        if cfg.get("transformer"):
            self._logs.put_transformer(
                logGroupIdentifier=name,
                transformerConfig=list(cfg["transformer"]),
            )
        elif "transformer" in cfg:
            self._delete_log_feature("delete_transformer", logGroupIdentifier=name)
        self._reconcile_log_filters(name, cfg)

    def _reconcile_log_policy(self, name: str, cfg: dict[str, Any], key: str) -> None:
        if key not in cfg:
            return
        operation = {
            "data_protection_policy": "data_protection_policy",
            "index_policy": "index_policy",
        }[key]
        value = cfg.get(key)
        if value is None:
            self._delete_log_feature(f"delete_{operation}", logGroupIdentifier=name)
            return
        getattr(self._logs, f"put_{operation}")(
            logGroupIdentifier=name,
            policyDocument=_json_document(value),
        )

    def _delete_log_feature(self, method: str, **kwargs: Any) -> None:
        try:
            getattr(self._logs, method)(**kwargs)
        except Exception as exc:
            if not _not_found(exc):
                raise

    def _reconcile_log_filters(self, name: str, cfg: dict[str, Any]) -> None:
        desired_metrics = {str(item["name"]): dict(item["request"]) for item in cfg.get("metric_filters") or []}
        for filter_name, request in desired_metrics.items():
            self._logs.put_metric_filter(logGroupName=name, filterName=filter_name, **request)
        existing_metrics = self._logs.describe_metric_filters(logGroupName=name).get("metricFilters") or []
        for item in existing_metrics:
            filter_name = str(item.get("filterName") or "")
            if filter_name and filter_name not in desired_metrics:
                self._logs.delete_metric_filter(logGroupName=name, filterName=filter_name)
        desired_subscriptions = {
            str(item["name"]): dict(item["request"]) for item in cfg.get("subscription_filters") or []
        }
        for filter_name, request in desired_subscriptions.items():
            self._logs.put_subscription_filter(logGroupName=name, filterName=filter_name, **request)
        existing_subscriptions = (
            self._logs.describe_subscription_filters(
                logGroupName=name,
            ).get("subscriptionFilters")
            or []
        )
        for item in existing_subscriptions:
            filter_name = str(item.get("filterName") or "")
            if filter_name and filter_name not in desired_subscriptions:
                self._logs.delete_subscription_filter(logGroupName=name, filterName=filter_name)

    def _reconcile_alarms(self, bundle_id: str, cfg: dict[str, Any], tags: dict[str, str]) -> set[str]:
        desired: set[str] = set()
        for collection, method in (
            ("metric_alarms", "put_metric_alarm"),
            ("composite_alarms", "put_composite_alarm"),
        ):
            for declaration in cfg.get(collection) or []:
                name = str(declaration["name"])
                desired.add(name)
                current = self._alarm(name)
                if current is not None:
                    self._assert_cw_owned(str(current["AlarmArn"]), name, bundle_id)
                request = dict(declaration["request"])
                getattr(self._cw, method)(AlarmName=name, Tags=_tag_list(tags), **request)
                alarm = self._alarm(name)
                if alarm is not None:
                    self._cw.tag_resource(ResourceARN=str(alarm["AlarmArn"]), Tags=_tag_list(tags))
        return desired

    def _reconcile_dashboards(
        self,
        bundle_id: str,
        cfg: dict[str, Any],
        tags: dict[str, str],
        log_group: str,
        *,
        log_class: str,
    ) -> set[str]:
        declaration = cfg.get("dashboard", self._config.dashboard_enabled_default)
        if declaration is False:
            return set()
        dashboard = declaration if isinstance(declaration, dict) else {}
        name = str(dashboard.get("name") or _dashboard_name(bundle_id))
        existing = self._dashboard_entry(name)
        if existing is not None:
            self._assert_cw_owned(str(existing.get("DashboardArn") or ""), name, bundle_id)
        body = (
            dashboard["body"]
            if dashboard.get("body") is not None
            else _default_dashboard(
                log_group,
                self._config.region,
                log_class=log_class,
                alarm_names=[
                    str(item["name"])
                    for collection in ("metric_alarms", "composite_alarms")
                    for item in cfg.get(collection) or []
                ],
                account_id=self._config.account_id,
            )
        )
        response = self._cw.put_dashboard(
            DashboardName=name,
            DashboardBody=_json_document(body),
        )
        messages = response.get("DashboardValidationMessages") or []
        if messages and not dashboard.get("allow_validation_messages", False):
            raise ManagedServiceError(
                "CloudWatch dashboard validation failed: "
                + "; ".join(str(item.get("Message") or item) for item in messages),
            )
        entry = self._dashboard_entry(name)
        arn = str((entry or {}).get("DashboardArn") or _dashboard_arn(self._config, name))
        if not arn:
            raise ManagedServiceError("CloudWatch dashboard ARN could not be resolved for tagging")
        self._cw.tag_resource(ResourceARN=arn, Tags=_tag_list(tags))
        return {name}

    def _reconcile_insights(self, bundle_id: str, cfg: dict[str, Any], tags: dict[str, str]) -> set[str]:
        desired: set[str] = set()
        existing = {str(item.get("Name") or ""): item for item in self._owned_insight_rules(bundle_id)}
        for declaration in cfg.get("insight_rules") or []:
            name = str(declaration["name"])
            desired.add(name)
            if name not in existing:
                foreign = self._insight_rule(name)
                if foreign is not None:
                    self._assert_cw_owned(str(foreign.get("RuleArn") or ""), name, bundle_id)
            self._cw.put_insight_rule(RuleName=name, Tags=_tag_list(tags), **dict(declaration["request"]))
            rule = self._insight_rule(name)
            if rule and rule.get("RuleArn"):
                self._cw.tag_resource(ResourceARN=str(rule["RuleArn"]), Tags=_tag_list(tags))
        return desired

    def _reconcile_streams(self, bundle_id: str, cfg: dict[str, Any], tags: dict[str, str]) -> set[str]:
        desired: set[str] = set()
        existing = {str(item.get("Name") or ""): item for item in self._owned_metric_streams(bundle_id)}
        for declaration in cfg.get("metric_streams") or []:
            name = str(declaration["name"])
            desired.add(name)
            if name not in existing:
                foreign = self._metric_stream(name)
                if foreign is not None:
                    self._assert_cw_owned(str(foreign.get("Arn") or ""), name, bundle_id)
            response = self._cw.put_metric_stream(Name=name, Tags=_tag_list(tags), **dict(declaration["request"]))
            arn = str(response.get("Arn") or (self._metric_stream(name) or {}).get("Arn") or "")
            if arn:
                self._cw.tag_resource(ResourceARN=arn, Tags=_tag_list(tags))
        return desired

    def _prune_cloudwatch_resources(
        self,
        bundle_id: str,
        *,
        alarms: set[str],
        dashboards: set[str],
        insights: set[str],
        streams: set[str],
    ) -> None:
        alarm_names = {str(item.get("AlarmName") or "") for item in self._owned_alarms(bundle_id)} - alarms
        if alarm_names:
            self._cw.delete_alarms(AlarmNames=sorted(alarm_names))
        dashboard_names = {
            str(item.get("DashboardName") or "") for item in self._owned_dashboards(bundle_id)
        } - dashboards
        if dashboard_names:
            self._cw.delete_dashboards(DashboardNames=sorted(dashboard_names))
        insight_names = {str(item.get("Name") or "") for item in self._owned_insight_rules(bundle_id)} - insights
        if insight_names:
            response = self._cw.delete_insight_rules(RuleNames=sorted(insight_names))
            failures = response.get("Failures") or []
            if failures:
                raise ManagedServiceError(
                    "CloudWatch failed to delete Contributor Insights rules: "
                    + "; ".join(
                        f"{item.get('FailureResource', 'unknown')}: {item.get('ExceptionDescription', 'unknown error')}"
                        for item in failures
                    ),
                )
        stream_names = {str(item.get("Name") or "") for item in self._owned_metric_streams(bundle_id)} - streams
        for name in sorted(stream_names):
            self._cw.delete_metric_stream(Name=name)

    def _delete_cloudwatch_resources(self, bundle_id: str) -> None:
        self._prune_cloudwatch_resources(
            bundle_id,
            alarms=set(),
            dashboards=set(),
            insights=set(),
            streams=set(),
        )

    def _log_group(self, name: str) -> dict[str, Any] | None:
        response = self._logs.describe_log_groups(logGroupNamePrefix=name, limit=50)
        return next(
            (dict(item) for item in response.get("logGroups") or [] if item.get("logGroupName") == name),
            None,
        )

    def _owned_log_groups(self, bundle_id: str) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"logGroupTags": {_BUNDLE_TAG: bundle_id}, "limit": 50}
            if token:
                request["nextToken"] = token
            response = self._logs.list_log_groups(**request)
            groups.extend(dict(item) for item in response.get("logGroups") or [])
            token = str(response.get("nextToken") or "")
            if not token:
                return groups

    def _assert_log_group_owned(self, group: dict[str, Any], name: str, bundle_id: str) -> None:
        arn = _log_group_arn(group)
        tags = self._logs.list_tags_for_resource(resourceArn=arn).get("tags") or {}
        if tags.get(_BUNDLE_TAG) != bundle_id:
            raise ManagedServiceError(f"refusing to adopt foreign CloudWatch log group {name!r}")

    def _alarm(self, name: str) -> dict[str, Any] | None:
        response = self._cw.describe_alarms(AlarmNames=[name])
        alarms = [*(response.get("MetricAlarms") or []), *(response.get("CompositeAlarms") or [])]
        return dict(alarms[0]) if alarms else None

    def _owned_alarms(self, bundle_id: str) -> list[dict[str, Any]]:
        alarms: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {}
            if token:
                request["NextToken"] = token
            response = self._cw.describe_alarms(**request)
            candidates = [*(response.get("MetricAlarms") or []), *(response.get("CompositeAlarms") or [])]
            alarms.extend(
                dict(item)
                for item in candidates
                if self._cw_tags(str(item.get("AlarmArn") or "")).get(_BUNDLE_TAG) == bundle_id
            )
            token = str(response.get("NextToken") or "")
            if not token:
                return alarms

    def _dashboard(self, name: str) -> dict[str, Any] | None:
        try:
            return dict(self._cw.get_dashboard(DashboardName=name))
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _dashboard_entry(self, name: str) -> dict[str, Any] | None:
        response = self._cw.list_dashboards(DashboardNamePrefix=name)
        return next(
            (dict(item) for item in response.get("DashboardEntries") or [] if item.get("DashboardName") == name),
            None,
        )

    def _owned_dashboards(self, bundle_id: str) -> list[dict[str, Any]]:
        dashboards: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {}
            if token:
                request["NextToken"] = token
            response = self._cw.list_dashboards(**request)
            dashboards.extend(
                dict(item)
                for item in response.get("DashboardEntries") or []
                if self._cw_tags(str(item.get("DashboardArn") or "")).get(_BUNDLE_TAG) == bundle_id
            )
            token = str(response.get("NextToken") or "")
            if not token:
                return dashboards

    def _insight_rule(self, name: str) -> dict[str, Any] | None:
        return next((item for item in self._all_insight_rules() if item.get("Name") == name), None)

    def _owned_insight_rules(self, bundle_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self._all_insight_rules()
            if self._cw_tags(str(item.get("RuleArn") or "")).get(_BUNDLE_TAG) == bundle_id
        ]

    def _all_insight_rules(self) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {}
            if token:
                request["NextToken"] = token
            response = self._cw.describe_insight_rules(**request)
            rules.extend(dict(item) for item in response.get("InsightRules") or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return rules

    def _metric_stream(self, name: str) -> dict[str, Any] | None:
        try:
            return dict(self._cw.get_metric_stream(Name=name))
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _owned_metric_streams(self, bundle_id: str) -> list[dict[str, Any]]:
        streams: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {}
            if token:
                request["NextToken"] = token
            response = self._cw.list_metric_streams(**request)
            streams.extend(
                dict(item)
                for item in response.get("Entries") or []
                if self._cw_tags(str(item.get("Arn") or "")).get(_BUNDLE_TAG) == bundle_id
            )
            token = str(response.get("NextToken") or "")
            if not token:
                return streams

    def _assert_cw_owned(self, arn: str, name: str, bundle_id: str) -> None:
        if not arn or self._cw_tags(arn).get(_BUNDLE_TAG) != bundle_id:
            raise ManagedServiceError(f"refusing to adopt foreign CloudWatch resource {name!r}")

    def _cw_tags(self, arn: str) -> dict[str, str]:
        if not arn:
            return {}
        response = self._cw.list_tags_for_resource(ResourceARN=arn)
        return {str(item.get("Key")): str(item.get("Value")) for item in response.get("Tags") or []}

    def _default_log_group(self, bundle_id: str) -> str:
        prefix = self._config.log_group_prefix.rstrip("/") or "/astrolift"
        return f"{prefix}/{bundle_id}"[:512]


def _log_config(cfg: dict[str, Any], defaults: CloudWatchConfig) -> dict[str, Any]:
    value = cfg.get("log_group", True)
    if value is False:
        return {"enabled": False}
    if value is True or value is None:
        return {
            "enabled": True,
            "retention_days": defaults.retention_days_default,
            "class": defaults.log_group_class_default,
        }
    return dict(value) if isinstance(value, dict) else value


def _validate_declarations(collection: str, value: Any) -> str:
    if not isinstance(value, list):
        return f"config.{collection} must be an array"
    names: set[str] = set()
    for index, declaration in enumerate(value):
        if not isinstance(declaration, dict):
            return f"config.{collection}[{index}] must be an object"
        raw_name = declaration.get("name")
        if not isinstance(raw_name, str) or not raw_name or not isinstance(declaration.get("request"), dict):
            return f"config.{collection}[{index}] requires name and request"
        name = raw_name
        if name in names:
            return f"config.{collection} names must be unique"
        names.add(name)
        if collection in {"metric_alarms", "composite_alarms", "metric_streams"} and len(name) > 255:
            return f"config.{collection}[{index}].name exceeds 255 characters"
        if collection == "insight_rules" and (
            len(name) > 128 or not all(32 <= ord(character) <= 126 for character in name)
        ):
            return f"config.{collection}[{index}].name must be 1-128 printable ASCII characters"
        if collection in {"metric_filters", "subscription_filters"} and (len(name) > 512 or ":" in name or "*" in name):
            return f"config.{collection}[{index}].name is invalid"
        reserved = _RESERVED_NAMED_REQUESTS[collection].intersection(declaration["request"])
        if reserved:
            return f"config.{collection}[{index}].request cannot override Astrolift-owned fields: " + ", ".join(
                sorted(reserved)
            )
        missing = _REQUIRED_NAMED_REQUESTS[collection] - declaration["request"].keys()
        if missing:
            return f"config.{collection}[{index}].request is missing required fields: " + ", ".join(sorted(missing))
    return ""


def _bundle_id(spec: ProvisionSpec) -> str:
    raw = "-".join(
        (
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
        ),
    )
    return _name(raw, hashlib.sha256(raw.encode()).hexdigest()[:10], max_length=96)


def _name(*parts: str, max_length: int) -> str:
    value = re.sub(r"[^A-Za-z0-9_.#/-]+", "-", "-".join(str(part) for part in parts if part)).strip("-./")
    return (value or "astrolift")[:max_length]


def _dashboard_name(bundle_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", f"astrolift-{bundle_id}").strip("-")
    return (value or "astrolift")[:255]


def _bundle_tags(bundle_id: str, spec: ProvisionSpec | None) -> dict[str, str]:
    tags = {_BUNDLE_TAG: bundle_id, "astrolift.io/managed-by": "platform"}
    if spec is not None:
        tags.update({item["Key"]: item["Value"] for item in tags_for(spec)})
    return tags


def _tag_list(tags: dict[str, str]) -> list[dict[str, str]]:
    return [{"Key": key, "Value": value} for key, value in sorted(tags.items())]


def _json_document(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _default_dashboard(
    log_group: str,
    region: str,
    *,
    log_class: str,
    alarm_names: list[str],
    account_id: str,
) -> dict[str, Any]:
    widgets: list[dict[str, Any]] = []
    next_y = 0
    if log_group:
        if log_class == "DELIVERY":
            widgets.append(
                {
                    "type": "text",
                    "x": 0,
                    "y": 0,
                    "width": 24,
                    "height": 3,
                    "properties": {
                        "markdown": (
                            f"### Delivery log group `{log_group}`\n"
                            "CloudWatch Logs Insights is unavailable for the DELIVERY log class."
                        ),
                    },
                },
            )
            next_y = 3
        else:
            widgets.append(
                {
                    "type": "log",
                    "x": 0,
                    "y": 0,
                    "width": 24,
                    "height": 8,
                    "properties": {
                        "region": region,
                        "title": "Recent application logs",
                        "view": "table",
                        "query": (
                            f"SOURCE '{log_group}' | fields @timestamp, @message | sort @timestamp desc | limit 100"
                        ),
                    },
                },
            )
            next_y = 8
    if alarm_names and account_id:
        widgets.append(
            {
                "type": "metric",
                "x": 0,
                "y": next_y,
                "width": 24,
                "height": 6,
                "properties": {
                    "region": region,
                    "title": "Managed alarms",
                    "view": "timeSeries",
                    "annotations": {
                        "alarms": [
                            f"arn:aws:cloudwatch:{region}:{account_id}:alarm:{name}" for name in sorted(alarm_names)
                        ],
                    },
                },
            },
        )
    if not widgets:
        widgets.append(
            {
                "type": "text",
                "x": 0,
                "y": 0,
                "width": 24,
                "height": 3,
                "properties": {"markdown": "### Astrolift CloudWatch observability bundle"},
            },
        )
    return {"widgets": widgets}


def _log_group_arn(group: dict[str, Any]) -> str:
    return str(group.get("logGroupArn") or group.get("arn") or "").removesuffix(":*")


def _dashboard_arn(config: CloudWatchConfig, name: str) -> str:
    if not config.account_id:
        return ""
    return f"arn:aws:cloudwatch::{config.account_id}:dashboard/{name}"


def _dashboard_url(region: str, name: str) -> str:
    if not name:
        return ""
    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
        f"#dashboards:name={quote(name, safe='')}"
    )


def _binding_grants(log_group_arn: str, cfg: dict[str, Any], *, log_class: str) -> list[Grant]:
    mode = str(cfg.get("access_mode") or "write")
    grants: list[Grant] = []
    if mode in {"write", "both", "logs"} and log_group_arn and log_class != "DELIVERY":
        grants.append(
            Grant(
                resource=f"{log_group_arn}:*",
                actions=["logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"],
            ),
        )
    if mode in {"read", "both"} and log_group_arn and log_class in {"STANDARD", "INFREQUENT_ACCESS"}:
        group_actions = ["logs:StartQuery"]
        if log_class == "STANDARD":
            group_actions = ["logs:FilterLogEvents", "logs:GetLogEvents", *group_actions]
        grants.append(
            Grant(
                resource=log_group_arn,
                actions=group_actions,
            ),
        )
        grants.append(
            Grant(
                resource="*",
                actions=["logs:DescribeQueries", "logs:GetQueryResults", "logs:StopQuery"],
            ),
        )
    if mode in {"write", "both", "metrics"}:
        grants.append(Grant(resource="*", actions=["cloudwatch:PutMetricData"]))
    if mode in {"read", "both"}:
        grants.append(
            Grant(
                resource="*",
                actions=["cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"],
            ),
        )
    return grants


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    code = str((response.get("Error") or {}).get("Code") or "")
    return code in {"ResourceNotFoundException", "ResourceNotFound"} or "not found" in str(exc).lower()
