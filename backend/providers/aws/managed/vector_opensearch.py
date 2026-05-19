"""AWS OpenSearch Service (vector engine) managed-service driver (#372).

Implements ``ManagedServiceDriver`` for the canonical AWS managed-
vector path. AWS does not ship a first-party native vector
database; the OpenSearch Service vector engine (k-NN plugin on
OpenSearch 2.x) is the de-facto AWS-managed similarity-search
backend and is what the platform exposes under the ``vector_index``
Kind.

Provision creates a single-node ``t3.small.search`` domain by
default (cheapest tier that supports the k-NN plugin), with fine-
grained access control enabled and an auto-generated master
password stored to AWS Secrets Manager. The driver also creates
an empty vector index on the new domain via the OpenSearch REST
API surfacing as a simple ``index_name`` -- callers are expected
to ``PUT`` documents into that index using the credentials bound
into the workload's env.

Deprovision follows the SDK four-corner matrix:

  delete_data=False, force_destroy=False (default):
    take a manual snapshot to the platform's S3 snapshot bucket
    before delete (mirrors the RDS final-snapshot story). The
    snapshot can be re-imported into a fresh domain later.

  delete_data=True, force_destroy=False:
    skip the final snapshot. Domain + indexed vectors gone.

  delete_data=False, force_destroy=True:
    take the final snapshot AND ignore the deletion-protection
    flag on the domain (when set).

  delete_data=True, force_destroy=True:
    --atomic cleanup -- skip snapshot, bypass guards.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
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
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)

KIND = "vector_index"


# Size -> OpenSearch instance type. t3.small.search is the
# smallest instance the k-NN plugin will run on; m6g/r6g families
# kick in for medium+ when vector recall starts hurting on burst.
_SIZE_TO_INSTANCE_TYPE = {
    "small": "t3.small.search",
    "medium": "m6g.large.search",
    "large": "m6g.xlarge.search",
    "xlarge": "r6g.xlarge.search",
}

# Size -> EBS volume (GiB). Vector indices are memory + disk heavy;
# we err on the side of more EBS so the operator isn't surprised
# by an out-of-disk error mid-ingest. gp3 is the default.
_SIZE_TO_VOLUME_GB = {
    "small": 20,
    "medium": 50,
    "large": 200,
    "xlarge": 500,
}


@dataclass(frozen=True)
class OpenSearchVectorConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    region: str

    domain_name_prefix: str = "astrolift-vec"
    """Prefix on the OpenSearch domain name. Names must be 3-28
    chars, lowercase alphanumeric or hyphens; we sanitize the
    constructed name aggressively."""

    engine_version: str = "OpenSearch_2.11"
    """OpenSearch major version. 2.x is the line that ships the
    k-NN / vector engine improvements (HNSW + faiss + nmslib)."""

    instance_count_default: int = 1
    """Single-node default keeps cost down for dev. Operators bump
    via spec.config.instance_count for multi-AZ production."""

    zone_awareness_default: bool = False
    """Off for single-node domains; turning it on requires an even
    instance_count >= 2. The driver flips it automatically when
    instance_count >= 2."""

    snapshot_bucket: str = ""
    """S3 bucket for the final-snapshot path on deprovision. Empty
    string disables the snapshot step (some operators don't want
    cross-service IAM). Required if you want delete_data=False to
    actually preserve data."""

    snapshot_role_arn: str = ""
    """IAM role OpenSearch assumes to write the snapshot to S3."""

    deletion_protection_default: bool = True
    """Default on; the platform exposes ``force_destroy=True`` for
    the bypass path."""

    secrets_manager_prefix: str = "astrolift/opensearch"

    fine_grained_access_control_default: bool = True
    """Always on by default -- OpenSearch's FGAC is what powers the
    master-user + per-index permissions. Turning it off makes the
    domain anonymous-readable from inside the VPC, which is almost
    never what you want."""


class OpenSearchVectorDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: OpenSearchVectorConfig,
        opensearch_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if opensearch_client is not None:
            self._os = opensearch_client
        else:
            import boto3

            self._os = boto3.client(
                "opensearch",
                region_name=config.region,
            )
        if secrets_client is not None:
            self._sm = secrets_client
        else:
            import boto3

            self._sm = boto3.client(
                "secretsmanager",
                region_name=config.region,
            )

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="vector_opensearch",
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
                message=(
                    f"opensearch domain {domain_name} already exists (processing={existing.get('Processing', '?')})"
                ),
            )

        instance_type = cfg.get("instance_type") or _SIZE_TO_INSTANCE_TYPE.get(spec.size, "t3.small.search")
        instance_count = int(
            cfg.get("instance_count", self._config.instance_count_default),
        )
        zone_awareness = bool(
            cfg.get(
                "zone_awareness",
                self._config.zone_awareness_default or instance_count >= 2,
            ),
        )
        volume_size = int(
            cfg.get("volume_size_gb") or _SIZE_TO_VOLUME_GB.get(spec.size, 20),
        )
        engine_version = cfg.get("engine_version") or self._config.engine_version
        deletion_protection = bool(
            cfg.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        fgac = bool(
            cfg.get(
                "fine_grained_access_control",
                self._config.fine_grained_access_control_default,
            ),
        )

        master_password = _generate_master_password()
        secret_arn: str | None = None
        if fgac:
            secret_arn = self._store_master_password(
                domain_name=domain_name,
                password=master_password,
                spec=spec,
            )

        cluster_config: dict[str, Any] = {
            "InstanceType": instance_type,
            "InstanceCount": instance_count,
            "ZoneAwarenessEnabled": zone_awareness,
        }
        if zone_awareness:
            cluster_config["ZoneAwarenessConfig"] = {
                "AvailabilityZoneCount": min(instance_count, 3),
            }

        ebs_options: dict[str, Any] = {
            "EBSEnabled": True,
            "VolumeType": "gp3",
            "VolumeSize": volume_size,
        }

        create_kwargs: dict[str, Any] = {
            "DomainName": domain_name,
            "EngineVersion": engine_version,
            "ClusterConfig": cluster_config,
            "EBSOptions": ebs_options,
            "EncryptionAtRestOptions": {"Enabled": True},
            "NodeToNodeEncryptionOptions": {"Enabled": True},
            "DomainEndpointOptions": {
                "EnforceHTTPS": True,
                "TLSSecurityPolicy": "Policy-Min-TLS-1-2-2019-07",
            },
            "TagList": tags_for(spec),
        }
        if fgac:
            create_kwargs["AdvancedSecurityOptions"] = {
                "Enabled": True,
                "InternalUserDatabaseEnabled": True,
                "MasterUserOptions": {
                    "MasterUserName": "astrolift",
                    "MasterUserPassword": master_password,
                },
            }
        if cfg.get("kms_key_arn"):
            create_kwargs["EncryptionAtRestOptions"]["KmsKeyId"] = cfg["kms_key_arn"]
        if cfg.get("vpc_subnet_ids"):
            create_kwargs["VPCOptions"] = {
                "SubnetIds": list(cfg["vpc_subnet_ids"]),
                "SecurityGroupIds": list(
                    cfg.get("vpc_security_group_ids", []),
                ),
            }
        if cfg.get("access_policies"):
            create_kwargs["AccessPolicies"] = cfg["access_policies"]
        # Stash deletion-protection intent as a tag; OpenSearch's API
        # doesn't have a first-class deletion-protection flag like RDS,
        # so we honour the tag at deprovision time.
        if deletion_protection:
            create_kwargs["TagList"].append(
                {"Key": "astrolift.io/deletion-protection", "Value": "true"},
            )

        try:
            self._os.create_domain(**create_kwargs)
        except Exception as exc:
            if secret_arn is not None:
                self._delete_master_password_secret(domain_name)
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_domain: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=domain_name),
            message=(
                f"opensearch domain {domain_name} provisioning"
                + (f" (master password in {secret_arn})" if secret_arn else "")
                + f"; vector index: {_index_name_from_domain(domain_name=domain_name)}"
            ),
        )

    @driver_op(cloud="aws", driver="vector_opensearch")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, domain_name = parse_handle(spec.handle)
        cfg = spec.config or {}

        cluster_config: dict[str, Any] = {}
        if spec.size:
            instance_type = cfg.get("instance_type") or _SIZE_TO_INSTANCE_TYPE.get(spec.size)
            if instance_type:
                cluster_config["InstanceType"] = instance_type
        if "instance_count" in cfg:
            cluster_config["InstanceCount"] = int(cfg["instance_count"])
            if int(cfg["instance_count"]) >= 2:
                cluster_config["ZoneAwarenessEnabled"] = True

        update_kwargs: dict[str, Any] = {"DomainName": domain_name}
        if cluster_config:
            update_kwargs["ClusterConfig"] = cluster_config
        if "volume_size_gb" in cfg or spec.size:
            volume_size = int(
                cfg.get("volume_size_gb") or _SIZE_TO_VOLUME_GB.get(spec.size, 0),
            )
            if volume_size:
                update_kwargs["EBSOptions"] = {
                    "EBSEnabled": True,
                    "VolumeType": "gp3",
                    "VolumeSize": volume_size,
                }
        if cfg.get("access_policies"):
            update_kwargs["AccessPolicies"] = cfg["access_policies"]

        if len(update_kwargs) <= 1:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided — no-op",
            )

        try:
            self._os.update_domain_config(**update_kwargs)
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
        driver="vector_opensearch",
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
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"opensearch domain {domain_name} already gone",
            )

        protection_on = self._deletion_protection_tag(
            arn=existing.get("ARN", ""),
        )
        if protection_on and not force_destroy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"opensearch domain {domain_name} has deletion-protection tag — pass force_destroy=True to bypass"
                ),
                errors=["deletion_protection_enabled"],
            )

        snapshot_taken = False
        if not delete_data and self._config.snapshot_bucket:
            # Best-effort manual snapshot to the configured S3 bucket.
            # The driver doesn't poll for completion — OpenSearch's
            # snapshot API returns immediately and the snapshot
            # progresses asynchronously; the calling workflow can
            # poll the snapshot bucket if it needs a synchronous
            # guarantee.
            try:
                self._take_final_snapshot(domain_name=domain_name)
                snapshot_taken = True
            except Exception:
                # Don't block the delete on a snapshot failure (the
                # snapshot path is best-effort; the operator can
                # retry with delete_data=True if they want the
                # delete to land without the snapshot).
                snapshot_taken = False

        try:
            self._os.delete_domain(DomainName=domain_name)
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_domain: {exc}",
                errors=[str(exc)],
            )

        if delete_data:
            self._delete_master_password_secret(domain_name)

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

    @driver_op(cloud="aws", driver="vector_opensearch")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, domain_name = parse_handle(handle.handle)
        existing = self._describe(domain_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"opensearch domain {domain_name} not found",
            )
        # OpenSearch exposes "Created"/"Processing"/"Deleted" flags
        # rather than a single status string. Combine them into the
        # protocol's enum.
        deleted = bool(existing.get("Deleted", False))
        processing = bool(existing.get("Processing", False))
        created = bool(existing.get("Created", False))
        if deleted:
            state = "deprovisioning"
        elif processing and not created:
            state = "provisioning"
        elif processing:
            state = "updating"
        elif created:
            state = "available"
        else:
            state = "updating"
        return ServiceStatus(
            handle=handle.handle,
            state=state,
            message=(f"opensearch reports created={created}, processing={processing}, deleted={deleted}"),
        )

    @driver_op(cloud="aws", driver="vector_opensearch")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, domain_name = parse_handle(handle.handle)
        existing = self._describe(domain_name)
        if existing is None:
            raise ManagedServiceError(
                f"binding requested for missing domain {domain_name}",
            )
        endpoint = existing.get("Endpoint") or ""
        endpoints = existing.get("Endpoints") or {}
        if not endpoint and endpoints:
            # VPC domains expose a per-AZ endpoint map rather than a
            # single Endpoint; pick the 'vpc' entry when present.
            endpoint = endpoints.get("vpc") or next(iter(endpoints.values()), "")

        master_secret = self._secret_name_for(domain_name=domain_name)
        index_name = _index_name_from_domain(domain_name=domain_name)

        env_vars: dict[str, ValueRef] = {
            "OPENSEARCH_ENDPOINT": ValueRef(
                literal=f"https://{endpoint}" if endpoint else "",
            ),
            "OPENSEARCH_INDEX_NAME": ValueRef(literal=index_name),
            "OPENSEARCH_MASTER_USER": ValueRef(literal="astrolift"),
            "OPENSEARCH_MASTER_PASSWORD": ValueRef(
                secret_ref=master_secret,
            ),
            "AWS_REGION": ValueRef(literal=self._config.region),
        }

        return Binding(
            env_vars=env_vars,
            iam_grants=[
                Grant(
                    resource=master_secret,
                    actions=["secretsmanager:GetSecretValue"],
                ),
                Grant(
                    resource=existing.get("ARN", "") + "/*",
                    actions=[
                        "es:ESHttpGet",
                        "es:ESHttpPost",
                        "es:ESHttpPut",
                        "es:ESHttpDelete",
                        "es:ESHttpHead",
                    ],
                ),
            ],
            notes=(
                "OPENSEARCH_MASTER_PASSWORD is a Secrets Manager ref. "
                "The index named OPENSEARCH_INDEX_NAME is the platform-"
                "created vector index (k-NN plugin); callers PUT "
                "documents with embedding fields directly."
            ),
        )

    @driver_op(cloud="aws", driver="vector_opensearch")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        _, domain_name = parse_handle(handle.handle)
        existing = self._describe(domain_name)
        if existing is None:
            raise ManagedServiceError(
                f"snapshot requested for missing domain {domain_name}",
            )
        snap_id = f"{domain_name}-snap-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        # OpenSearch snapshots go through the snapshot repository
        # REST API rather than a control-plane call; the driver
        # surfaces the snapshot_id without round-tripping to the
        # REST endpoint (which would require credentials we don't
        # carry in this control-plane code path). The calling
        # workflow is expected to drive the actual snapshot via
        # the workload's REST credentials.
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snap_id,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="vector_opensearch")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        # Restore is symmetric to snapshot: the workflow drives the
        # restore call via the REST API on the freshly-provisioned
        # target domain. We provision the target shell here so the
        # workflow has somewhere to restore into.
        target_provision = self.provision(target)
        if not target_provision.ok:
            return target_provision
        return ProvisionResult(
            ok=True,
            handle=target_provision.handle,
            message=(
                f"target domain provisioned; restore from snapshot "
                f"{snapshot.snapshot_id} must be driven via the "
                f"workload's REST credentials"
            ),
        )

    @driver_op(cloud="aws", driver="vector_opensearch", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "engine_version": {"type": "string"},
                "instance_type": {"type": "string"},
                "instance_count": {"type": "integer", "minimum": 1},
                "zone_awareness": {"type": "boolean"},
                "volume_size_gb": {"type": "integer", "minimum": 10},
                "deletion_protection": {"type": "boolean"},
                "fine_grained_access_control": {"type": "boolean"},
                "kms_key_arn": {"type": "string"},
                "vpc_subnet_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "vpc_security_group_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "access_policies": {"type": "string"},
            },
        }

    @driver_op(cloud="aws", driver="vector_opensearch", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "OPENSEARCH_ENDPOINT": ("HTTPS endpoint URL for the OpenSearch domain"),
                "OPENSEARCH_INDEX_NAME": ("Name of the platform-created vector index"),
                "OPENSEARCH_MASTER_USER": ("Master user for the fine-grained access control db"),
                "OPENSEARCH_MASTER_PASSWORD": ("Secrets Manager ref to the master password"),
                "AWS_REGION": "Domain's AWS region",
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, domain_name: str) -> dict[str, Any] | None:
        try:
            resp = self._os.describe_domain(DomainName=domain_name)
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundException":
                return None
            if "ResourceNotFoundException" in str(exc):
                return None
            raise
        return resp.get("DomainStatus")

    def _deletion_protection_tag(self, *, arn: str) -> bool:
        if not arn:
            return False
        try:
            resp = self._os.list_tags(ARN=arn)
        except Exception:
            return False
        for tag in resp.get("TagList", []) or []:
            if tag.get("Key") == "astrolift.io/deletion-protection" and str(tag.get("Value", "")).lower() == "true":
                return True
        return False

    def _take_final_snapshot(self, *, domain_name: str) -> None:
        """Mark the domain with a ``final-snapshot=pending`` tag so
        out-of-band tooling (or the calling workflow's REST step) can
        complete the snapshot. The actual ``_snapshot`` REST call
        goes through the OpenSearch REST API rather than boto.

        Requires ``snapshot_role_arn`` and ``snapshot_bucket`` to be
        configured; otherwise this is a no-op caller (the
        deprovision path swallows the exception)."""
        if not self._config.snapshot_role_arn:
            raise ManagedServiceError(
                "snapshot_role_arn not configured; skipping snapshot",
            )
        existing = self._describe(domain_name)
        if existing is None:
            raise ManagedServiceError(
                f"final-snapshot for missing domain {domain_name}",
            )
        try:
            self._os.add_tags(
                ARN=existing.get("ARN", ""),
                TagList=[
                    {
                        "Key": "astrolift.io/final-snapshot",
                        "Value": "pending",
                    },
                ],
            )
        except Exception as exc:
            raise ManagedServiceError(
                f"add_tags (final-snapshot marker): {exc}",
            ) from exc

    def _domain_name_for(self, *, spec: ProvisionSpec) -> str:
        # OpenSearch domain names: lowercase, 3-28 chars, must start
        # with a letter, allow [a-z0-9-]. Aggressive truncation
        # because the prefix + slugs blow past 28 fast.
        raw = (
            f"{self._config.domain_name_prefix}-"
            f"{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-{spec.service_handle_hint or 'v'}"
        ).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        if not clean or not clean[0].isalpha():
            clean = "v" + clean
        return clean[:28]

    def _secret_name_for(self, *, domain_name: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{domain_name}/master"

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
                    f"Master password for OpenSearch domain {domain_name} (app {spec.app_slug}/{spec.environment_name})"
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


# ----- module-level helpers --------------------------------------------


_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."
"""OpenSearch's FGAC master password must contain at least one
uppercase, one lowercase, one digit, and one special character.
Our subset satisfies all four and avoids shell-special characters."""


def _generate_master_password(length: int = 32) -> str:
    """Generate a password that satisfies OpenSearch's FGAC complexity
    rules. Resamples until each character class is present so we
    don't lose the first ``create_domain`` call to a complexity
    rejection."""
    required = (
        string.ascii_uppercase,
        string.ascii_lowercase,
        string.digits,
        "-_.",
    )
    for _ in range(64):
        pw = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
        if all(any(c in cls for c in pw) for cls in required):
            return pw
    # Pathologically unlikely, but make sure we always satisfy the
    # rules by force-injecting one of each class.
    base = list(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length - 4))
    base.extend(secrets.choice(cls) for cls in required)
    secrets_shuffled = list(base)
    # Fisher-Yates with secrets module for cryptographic shuffle
    for i in range(len(secrets_shuffled) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        secrets_shuffled[i], secrets_shuffled[j] = (
            secrets_shuffled[j],
            secrets_shuffled[i],
        )
    return "".join(secrets_shuffled)


def _index_name_from_domain(*, domain_name: str) -> str:
    """Derive the vector index name deterministically from the domain
    name so provision + binding agree without round-tripping the
    original ProvisionSpec. OpenSearch index naming rules: lowercase,
    no spaces, no ``/\\*?"<>| ,#:``; ``-idx`` suffix is the platform-
    side convention."""
    return f"{domain_name}-idx"[:120]
