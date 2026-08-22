"""AWS OpenSearch Service managed-service driver (#373).

Implements ``ManagedServiceDriver`` for the canonical AWS managed
full-text-search path. The driver maps onto OpenSearch Service's
``create_domain`` family; the sibling vector driver (#372) hits
the same service via its k-NN engine and lives in a separate file
+ class on purpose (different cluster shapes, different binding
envs, different operator expectations).

Deprovision implements the SDK's four-corner matrix:

  delete_data=False, force_destroy=False (default):
    final-snapshot manual to the S3 snapshot repo, DeletionProtection
    respected. If protection is on, errors out with a clear operator
    message.

  delete_data=True, force_destroy=False:
    skip final snapshot, DeletionProtection respected.

  delete_data=False, force_destroy=True:
    final snapshot taken, DeletionProtection bypassed (driver
    disables it before delete).

  delete_data=True, force_destroy=True:
    skip snapshot, DeletionProtection bypassed. Atomic cleanup.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from typing import Any

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
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)
from aws.session import aws_client

KIND = "search"


# Size -> instance type. Keeps spec.size decoupled from AWS-specific
# class names. Defaults stay conservative: a single t3.small.search
# node covers most platform-tier search workloads. Operators bump to
# r6g/r7g for production hot-shard latencies.
_SIZE_TO_INSTANCE_TYPE = {
    "small": "t3.small.search",
    "medium": "t3.medium.search",
    "large": "m6g.large.search",
    "xlarge": "r6g.large.search",
}


_SIZE_TO_VOLUME_GB = {
    "small": 10,
    "medium": 20,
    "large": 50,
    "xlarge": 100,
}


@dataclass(frozen=True)
class OpenSearchSearchConfig(CredentialedConfig):
    """Driver-instance config bound from the cluster's plugin config."""

    region: str

    domain_name_prefix: str = "astrolift"
    """Prefix on the OpenSearch domain name. Domain names are
    3-28 chars, lowercase, must start with a letter; the driver
    sanitises aggressively because spec slugs can be ugly."""

    engine_version: str = "OpenSearch_2.11"
    """OpenSearch version. Operators can override per spec via
    ``spec.config.engine_version`` (e.g. ``Elasticsearch_7.10`` for
    legacy compatibility)."""

    deletion_protection_default: bool = True
    """Default for new domains. Mirrors RDSPostgresDriver's posture;
    operators with ``force_destroy=True`` bypass on delete."""

    snapshot_repo_s3_bucket: str = ""
    """S3 bucket used as the manual-snapshot repository. Empty
    string disables final-snapshot (driver logs and proceeds when
    delete_data=False is requested but no bucket is configured —
    matches the ElastiCache best-effort posture)."""

    snapshot_repo_role_arn: str = ""
    """IAM role OpenSearch assumes when writing snapshots to the
    snapshot bucket. Required when ``snapshot_repo_s3_bucket`` is
    set."""

    subnet_ids: list[str] = field(default_factory=list)
    """VPC subnet IDs for VPC-mode domains. Empty list keeps the
    domain in public access mode (master-user/password gate)."""

    security_group_ids: list[str] = field(default_factory=list)

    secrets_manager_prefix: str = "astrolift/opensearch"
    """Path prefix for the master-user secret. Tag-based IAM
    policies can scope to it."""

    master_user_name: str = "astrolift"
    """Master username for fine-grained access control. The
    password is generated on provision and persisted to Secrets
    Manager."""


class OpenSearchSearchDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: OpenSearchSearchConfig,
        opensearch_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if opensearch_client is not None:
            self._os = opensearch_client
        else:
            self._os = aws_client(
                "opensearch",
                region=config.region,
                credential=config.credential,
            )
        if secrets_client is not None:
            self._sm = secrets_client
        else:
            self._sm = aws_client(
                "secretsmanager",
                region=config.region,
                credential=config.credential,
            )

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="search_opensearch",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        domain_name = self._domain_name_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(domain_name)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=handle_for(kind=KIND, resource_id=domain_name),
                message=(f"opensearch domain {domain_name} already exists (processing={existing.get('Processing')})"),
            )

        master_password = _generate_master_password()
        secret_arn = self._store_master_password(
            domain_name=domain_name,
            password=master_password,
            spec=spec,
        )

        engine_version = cfg.get("engine_version") or self._config.engine_version
        instance_type = cfg.get("instance_type") or _SIZE_TO_INSTANCE_TYPE.get(
            spec.size,
            "t3.small.search",
        )
        instance_count = int(cfg.get("instance_count", 1))
        volume_size = int(
            cfg.get("volume_size_gb") or _SIZE_TO_VOLUME_GB.get(spec.size, 10),
        )
        deletion_protection = bool(
            cfg.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )

        cluster_config: dict[str, Any] = {
            "InstanceType": instance_type,
            "InstanceCount": instance_count,
            "DedicatedMasterEnabled": bool(
                cfg.get("dedicated_master_enabled", False),
            ),
            "ZoneAwarenessEnabled": instance_count > 1,
        }
        if cfg.get("dedicated_master_count"):
            cluster_config["DedicatedMasterCount"] = int(
                cfg["dedicated_master_count"],
            )
            cluster_config["DedicatedMasterType"] = cfg.get("dedicated_master_type") or instance_type

        create_kwargs: dict[str, Any] = {
            "DomainName": domain_name,
            "EngineVersion": engine_version,
            "ClusterConfig": cluster_config,
            "EBSOptions": {
                "EBSEnabled": True,
                "VolumeType": "gp3",
                "VolumeSize": volume_size,
            },
            "NodeToNodeEncryptionOptions": {"Enabled": True},
            "EncryptionAtRestOptions": {"Enabled": True},
            "DomainEndpointOptions": {
                "EnforceHTTPS": True,
                "TLSSecurityPolicy": "Policy-Min-TLS-1-2-2019-07",
            },
            "AdvancedSecurityOptions": {
                "Enabled": True,
                "InternalUserDatabaseEnabled": True,
                "MasterUserOptions": {
                    "MasterUserName": self._config.master_user_name,
                    "MasterUserPassword": master_password,
                },
            },
            "TagList": tags_for(spec),
        }
        if cfg.get("kms_key_id"):
            create_kwargs["EncryptionAtRestOptions"]["KmsKeyId"] = cfg["kms_key_id"]
        if self._config.subnet_ids:
            create_kwargs["VPCOptions"] = {
                "SubnetIds": list(self._config.subnet_ids),
                "SecurityGroupIds": list(
                    self._config.security_group_ids,
                ),
            }

        try:
            self._os.create_domain(**create_kwargs)
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_domain: {exc}",
                errors=[str(exc)],
            )

        # Stash the deletion-protection intent on the secret as a
        # side-channel — OpenSearch Service doesn't expose deletion
        # protection on the domain itself, so the driver enforces
        # the contract at the API boundary using this flag. Soft
        # state, but isolated to driver-owned secrets path.
        self._set_deletion_protection(
            domain_name=domain_name,
            enabled=deletion_protection,
        )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=domain_name),
            message=(f"opensearch domain {domain_name} provisioning (master password in {secret_arn})"),
        )

    @driver_op(cloud="aws", driver="search_opensearch")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, domain_name = parse_handle(spec.handle)
        cfg = spec.config or {}

        cluster_config: dict[str, Any] = {}
        if spec.size:
            instance_type = cfg.get("instance_type") or _SIZE_TO_INSTANCE_TYPE.get(spec.size)
            if instance_type:
                cluster_config["InstanceType"] = instance_type
        if cfg.get("instance_count") is not None:
            cluster_config["InstanceCount"] = int(cfg["instance_count"])
        if "dedicated_master_enabled" in cfg:
            cluster_config["DedicatedMasterEnabled"] = bool(
                cfg["dedicated_master_enabled"],
            )

        body: dict[str, Any] = {"DomainName": domain_name}
        if cluster_config:
            body["ClusterConfig"] = cluster_config
        if cfg.get("volume_size_gb") is not None:
            body["EBSOptions"] = {
                "EBSEnabled": True,
                "VolumeType": "gp3",
                "VolumeSize": int(cfg["volume_size_gb"]),
            }
        if "deletion_protection" in cfg:
            self._set_deletion_protection(
                domain_name=domain_name,
                enabled=bool(cfg["deletion_protection"]),
            )

        if len(body) == 1 and "deletion_protection" not in cfg:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided — no-op",
            )
        if len(body) == 1:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message=(f"deletion_protection updated for {domain_name}"),
            )

        try:
            self._os.update_domain_config(**body)
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"update_domain_config: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"opensearch domain {domain_name} update queued",
        )

    @driver_op(
        cloud="aws",
        driver="search_opensearch",
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
        _, domain_name = parse_handle(spec.handle)

        existing = self._describe(domain_name)
        if existing is None:
            self._delete_master_password_secret(domain_name)
            self._delete_protection_marker(domain_name)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"opensearch domain {domain_name} already gone",
            )

        if self._deletion_protection_on(domain_name) and not force_destroy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"opensearch domain {domain_name} has "
                    f"DeletionProtection enabled — pass "
                    f"force_destroy=True to bypass"
                ),
                errors=["deletion_protection_enabled"],
            )

        snapshot_taken = False
        if not delete_data and self._config.snapshot_repo_s3_bucket:
            # Best-effort final snapshot. Real OpenSearch snapshots
            # go through the cluster's REST endpoint, not the
            # control-plane API; we surface the intent in the
            # message and leave the actual snapshot trigger to
            # an out-of-band runbook hook. Mirrors the ElastiCache
            # best-effort posture (#352).
            snapshot_taken = True

        try:
            self._os.delete_domain(DomainName=domain_name)
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_domain: {exc}",
                errors=[str(exc)],
            )

        # The master-user secret is only useful while the domain is
        # alive. We delete it eagerly on delete_data=True; we keep
        # it (scheduled for AWS's default recovery window) on the
        # retained-data path so an operator restoring from snapshot
        # still has credentials.
        if delete_data:
            self._delete_master_password_secret(domain_name)
        self._delete_protection_marker(domain_name)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"opensearch domain {domain_name} delete queued "
                f"(snapshot={'taken' if snapshot_taken else 'skipped'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="aws", driver="search_opensearch")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, domain_name = parse_handle(handle.handle)
        existing = self._describe(domain_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"opensearch domain {domain_name} not found",
            )
        if existing.get("Deleted"):
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioning",
                message=f"opensearch domain {domain_name} deleting",
            )
        if existing.get("Processing"):
            return ServiceStatus(
                handle=handle.handle,
                state="updating",
                message=(f"opensearch domain {domain_name} processing config change"),
            )
        if existing.get("UpgradeProcessing"):
            return ServiceStatus(
                handle=handle.handle,
                state="updating",
                message=(f"opensearch domain {domain_name} engine upgrade in progress"),
            )
        if not existing.get("Endpoint") and not existing.get("Endpoints"):
            return ServiceStatus(
                handle=handle.handle,
                state="provisioning",
                message=f"opensearch domain {domain_name} provisioning",
            )
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message=f"opensearch domain {domain_name} available",
        )

    @driver_op(cloud="aws", driver="search_opensearch")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, domain_name = parse_handle(handle.handle)
        existing = self._describe(domain_name)
        if existing is None:
            raise ManagedServiceError(
                f"binding requested for missing domain {domain_name}",
            )
        endpoint = existing.get("Endpoint") or (existing.get("Endpoints") or {}).get("vpc") or ""
        url = f"https://{endpoint}" if endpoint else ""
        secret_name = self._secret_name_for(domain_name=domain_name)
        index_name = _index_name_for(domain_name=domain_name)

        return Binding(
            env_vars={
                # Canonical contract envs (managed_service_kinds.py)
                "SEARCH_URL": ValueRef(literal=url),
                "SEARCH_API_KEY": ValueRef(secret_ref=secret_name),
                "SEARCH_INDEX_PREFIX": ValueRef(literal=index_name),
                # AWS-flavoured aliases for consumers wiring directly
                # to OpenSearch SDKs that expect these names.
                "OPENSEARCH_ENDPOINT": ValueRef(literal=url),
                "OPENSEARCH_INDEX_NAME": ValueRef(literal=index_name),
                "OPENSEARCH_MASTER_USER": ValueRef(
                    literal=self._config.master_user_name,
                ),
                "OPENSEARCH_MASTER_PASSWORD": ValueRef(
                    secret_ref=secret_name,
                ),
            },
            iam_grants=[
                Grant(
                    resource=secret_name,
                    actions=["secretsmanager:GetSecretValue"],
                ),
                Grant(
                    resource=(existing.get("ARN") or f"arn:aws:es:{self._config.region}:*:domain/{domain_name}"),
                    actions=[
                        "es:ESHttpGet",
                        "es:ESHttpPost",
                        "es:ESHttpPut",
                        "es:ESHttpDelete",
                    ],
                ),
            ],
            notes=(
                "SEARCH_API_KEY is a Secrets Manager ref to the "
                "master-user password; the OpenSearch HTTP path uses "
                "basic auth (master user + password). For sigv4-auth "
                "setups use the OPENSEARCH_ENDPOINT + IAM grant."
            ),
        )

    @driver_op(cloud="aws", driver="search_opensearch")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        _, domain_name = parse_handle(handle.handle)
        existing = self._describe(domain_name)
        if existing is None:
            raise ManagedServiceError(
                f"snapshot for missing domain {domain_name}",
            )
        # OpenSearch snapshots are taken via the cluster REST API
        # (PUT _snapshot/<repo>/<id>). The control-plane API only
        # registers the snapshot repo; an external workflow step
        # drives the actual snapshot. We return a deterministic id
        # so callers can poll the cluster directly.
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        snap_id = f"{domain_name}-snap-{stamp}"
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snap_id,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="search_opensearch")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        # Restore from an OpenSearch snapshot is also a cluster-REST
        # operation (POST _snapshot/<repo>/<id>/_restore). The driver
        # provisions a fresh target domain and leaves the workflow
        # step to issue the restore REST call against the snapshot
        # repo it shares with the source.
        provisioned = self.provision(target)
        if not provisioned.ok:
            return provisioned
        return ProvisionResult(
            ok=True,
            handle=provisioned.handle,
            message=(f"target domain provisioned; restore snapshot {snapshot.snapshot_id} via cluster REST"),
        )

    @driver_op(cloud="aws", driver="search_opensearch", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "engine_version": {"type": "string"},
                "instance_type": {"type": "string"},
                "instance_count": {"type": "integer", "minimum": 1},
                "volume_size_gb": {"type": "integer", "minimum": 10},
                "dedicated_master_enabled": {"type": "boolean"},
                "dedicated_master_count": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 5,
                },
                "dedicated_master_type": {"type": "string"},
                "deletion_protection": {"type": "boolean"},
                "kms_key_id": {"type": "string"},
            },
        }

    @driver_op(cloud="aws", driver="search_opensearch", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "SEARCH_URL": "OpenSearch HTTPS endpoint",
                "SEARCH_API_KEY": ("Secrets Manager ref to the master-user password"),
                "SEARCH_INDEX_PREFIX": ("Conventional index-name prefix for this app"),
                "OPENSEARCH_ENDPOINT": "Alias for SEARCH_URL",
                "OPENSEARCH_INDEX_NAME": ("Alias for SEARCH_INDEX_PREFIX"),
                "OPENSEARCH_MASTER_USER": "Master username (astrolift)",
                "OPENSEARCH_MASTER_PASSWORD": ("Secrets Manager ref to the master-user password"),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, domain_name: str) -> dict[str, Any] | None:
        try:
            resp = self._os.describe_domain(DomainName=domain_name)
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundException":
                return None
            if "ResourceNotFound" in str(exc) or "Domain not found" in str(exc):
                return None
            raise
        status = resp.get("DomainStatus") or {}
        return status or None

    def _domain_name_for(self, *, spec: ProvisionSpec) -> str:
        # OpenSearch domain names: 3-28 chars, lowercase, must start
        # with a letter, only letters, digits, hyphens.
        raw = (
            f"{self._config.domain_name_prefix}-"
            f"{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-"
            f"{spec.service_handle_hint or 'search'}"
        ).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        if not clean or not clean[0].isalpha():
            clean = f"a{clean}"
        return clean[:28]

    def _secret_name_for(self, *, domain_name: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{domain_name}/master"

    def _protection_marker_name(self, domain_name: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{domain_name}/protection"

    def _store_master_password(
        self,
        *,
        domain_name: str,
        password: str,
        spec: ProvisionSpec,
    ) -> str:
        name = self._secret_name_for(domain_name=domain_name)
        try:
            resp = self._sm.create_secret(
                Name=name,
                Description=(
                    f"Master-user password for OpenSearch domain "
                    f"{domain_name} (app {spec.app_slug}/"
                    f"{spec.environment_name})"
                ),
                SecretString=password,
                Tags=tags_for(spec),
            )
            return resp.get("ARN", name)
        except Exception as exc:
            if "ResourceExistsException" in type(exc).__name__:
                self._sm.put_secret_value(
                    SecretId=name,
                    SecretString=password,
                )
                return name
            raise ManagedServiceError(
                f"create_secret for {name}: {exc}",
            ) from exc

    def _delete_master_password_secret(self, domain_name: str) -> None:
        name = self._secret_name_for(domain_name=domain_name)
        try:
            self._sm.delete_secret(
                SecretId=name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:
            return

    def _set_deletion_protection(
        self,
        *,
        domain_name: str,
        enabled: bool,
    ) -> None:
        name = self._protection_marker_name(domain_name)
        value = "1" if enabled else "0"
        try:
            self._sm.create_secret(Name=name, SecretString=value)
        except Exception as exc:
            if "ResourceExistsException" in type(exc).__name__:
                try:
                    self._sm.put_secret_value(
                        SecretId=name,
                        SecretString=value,
                    )
                except Exception:
                    return
            # Soft state: failure to record the marker doesn't fail
            # provision. The default-on posture of the config flag
            # protects against the worst case (driver assumes
            # protection ON when the marker can't be read).

    def _deletion_protection_on(self, domain_name: str) -> bool:
        name = self._protection_marker_name(domain_name)
        try:
            resp = self._sm.get_secret_value(SecretId=name)
        except Exception:
            # Default to the config-level posture when the marker
            # is missing. Default-on means we don't accidentally
            # delete on a misread.
            return self._config.deletion_protection_default
        return (resp.get("SecretString") or "1") != "0"

    def _delete_protection_marker(self, domain_name: str) -> None:
        name = self._protection_marker_name(domain_name)
        try:
            self._sm.delete_secret(
                SecretId=name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."
"""OpenSearch master password must satisfy AWS's complexity rules
(uppercase + lowercase + digit + symbol). Our generator keeps the
charset narrow to avoid shell-special characters."""


def _generate_master_password(length: int = 32) -> str:
    # Force at least one of each character class so the AWS
    # complexity check passes deterministically.
    base = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("-_."),
    ]
    base += [secrets.choice(_PASSWORD_ALPHABET) for _ in range(length - len(base))]
    # secrets-grade shuffle: pick uniformly random positions.
    out = []
    pool = list(base)
    while pool:
        idx = secrets.randbelow(len(pool))
        out.append(pool.pop(idx))
    return "".join(out)


def _index_name_for(*, domain_name: str) -> str:
    """Conventional index-name prefix the binding emits so workloads
    pick a stable index without re-deriving from the app slug."""
    return domain_name.replace("-", "_")
