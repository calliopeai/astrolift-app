"""GCP orphan scanner: Cloud SQL, Memorystore, Pub/Sub, GCS, IAM (spec 43 §3.2).

The AWS scan queries RDS, ElastiCache, SQS, S3 and IAM. These are the GCP
equivalents, one family per thing the campaign's manifests can create.

Two families deserve their reason stated.

*Pub/Sub subscriptions are scanned separately from topics.* A subscription
outlives the topic it was attached to, keeps accruing backlog, and is invisible
to anyone looking at topics. It is the most likely Pub/Sub residue, not the
least.

*Service accounts and project IAM bindings have no label surface at all.* GCP
service accounts do not support labels, so the only handle is the deterministic
account id, and a project IAM binding is a role plus a member string with no
metadata whatsoever. Both are matched by name, which works because every
campaign app carries the campaign slug in its name -- see ``_cert.campaign``.
A dangling ``roles/*`` binding for a deleted service account is exactly the
"no dangling IAM/role/grant" clause of VERIFY-CLEAN.

``GcpInventory`` is the seam. The live adapter below builds each family from the
same clients the GCP drivers use; tests drive the protocol with fakes, because
this is a scanner whose whole job is to be trusted when it says "clean".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from _cert.orphans.model import CloudResource, ScanReport, scan_families

if TYPE_CHECKING:
    from collections.abc import Iterable

    from _cert.campaign import Campaign

CLOUD = "gcp"


class GcpInventory(Protocol):
    """Read-only listings the scan needs. One method per resource family."""

    def cloud_sql_instances(self) -> Iterable[CloudResource]: ...

    def memorystore_instances(self) -> Iterable[CloudResource]: ...

    def pubsub_topics(self) -> Iterable[CloudResource]: ...

    def pubsub_subscriptions(self) -> Iterable[CloudResource]: ...

    def storage_buckets(self) -> Iterable[CloudResource]: ...

    def service_accounts(self) -> Iterable[CloudResource]: ...

    def project_iam_bindings(self) -> Iterable[CloudResource]: ...


def scan(inventory: GcpInventory, campaign: Campaign) -> ScanReport:
    """Everything in ``inventory`` that still belongs to ``campaign``.

    Call ``raise_if_dirty()`` on the result. Returning the report rather than
    raising here keeps a whole-campaign scan able to collect all three clouds
    before failing, but nothing may treat the report as advisory.
    """
    return scan_families(
        cloud=CLOUD,
        campaign=campaign,
        families=[
            ("cloud_sql", inventory.cloud_sql_instances),
            ("memorystore", inventory.memorystore_instances),
            ("pubsub_topic", inventory.pubsub_topics),
            ("pubsub_subscription", inventory.pubsub_subscriptions),
            ("gcs", inventory.storage_buckets),
            ("iam_service_account", inventory.service_accounts),
            ("iam_project_binding", inventory.project_iam_bindings),
        ],
    )


def _labels(obj: Any, *names: str) -> dict[str, str]:
    """Labels off a proto-Message or a dict, whichever the client returned.

    The GCP drivers carry the same dual-mode accessor for the same reason: the
    generated clients return protos, the REST fallbacks return dicts, and the
    scanner has to read both without caring which.
    """
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value:
            return {str(k): str(v) for k, v in dict(value).items()}
    return {}


def _attr(obj: Any, name: str, default: str = "") -> str:
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return str(value) if value else default


class LiveGcpInventory:
    """The credentialed adapter, built from the clients the drivers use.

    Untested against a live project -- Phase 2 is its first real run, by design:
    the campaign's whole shape is that nothing touches a cloud until the
    switch-on gate. The scan logic it feeds is covered offline with fakes.
    """

    def __init__(
        self,
        *,
        project_id: str,
        sql_client: Any | None = None,
        redis_client: Any | None = None,
        publisher_client: Any | None = None,
        subscriber_client: Any | None = None,
        storage_client: Any | None = None,
        iam_client: Any | None = None,
        project_iam_client: Any | None = None,
    ) -> None:
        self._project_id = project_id
        self._sql = sql_client
        self._redis = redis_client
        self._pub = publisher_client
        self._sub = subscriber_client
        self._storage = storage_client
        self._iam = iam_client
        self._project_iam = project_iam_client

    # -- lazy clients, mirroring how each driver constructs its own ----------

    def _sql_client(self) -> Any:
        if self._sql is None:
            from google.cloud import sql_v1

            self._sql = sql_v1.SqlInstancesServiceClient()
        return self._sql

    def _redis_client(self) -> Any:
        if self._redis is None:
            from google.cloud import redis_v1

            self._redis = redis_v1.CloudRedisClient()
        return self._redis

    def _publisher(self) -> Any:
        if self._pub is None:
            from google.cloud import pubsub_v1

            self._pub = pubsub_v1.PublisherClient()
        return self._pub

    def _subscriber(self) -> Any:
        if self._sub is None:
            from google.cloud import pubsub_v1

            self._sub = pubsub_v1.SubscriberClient()
        return self._sub

    def _storage_client(self) -> Any:
        if self._storage is None:
            from google.cloud import storage

            self._storage = storage.Client(project=self._project_id)
        return self._storage

    def _iam_client(self) -> Any:
        if self._iam is None:
            from google.cloud import iam_admin_v1

            self._iam = iam_admin_v1.IAMClient()
        return self._iam

    # -- families ------------------------------------------------------------

    def cloud_sql_instances(self) -> Iterable[CloudResource]:
        response = self._sql_client().list(project=self._project_id)
        for item in _items(response):
            yield CloudResource(
                identifier=_attr(item, "name"),
                location=_attr(item, "region"),
                tags=_labels(item, "user_labels", "userLabels", "settings"),
            )

    def memorystore_instances(self) -> Iterable[CloudResource]:
        parent = f"projects/{self._project_id}/locations/-"
        for item in self._redis_client().list_instances(parent=parent):
            yield CloudResource(
                identifier=_attr(item, "name"),
                location=_attr(item, "location_id") or _attr(item, "locationId"),
                tags=_labels(item, "labels"),
            )

    def pubsub_topics(self) -> Iterable[CloudResource]:
        project_path = f"projects/{self._project_id}"
        for item in self._publisher().list_topics(request={"project": project_path}):
            yield CloudResource(identifier=_attr(item, "name"), tags=_labels(item, "labels"))

    def pubsub_subscriptions(self) -> Iterable[CloudResource]:
        project_path = f"projects/{self._project_id}"
        for item in self._subscriber().list_subscriptions(request={"project": project_path}):
            yield CloudResource(identifier=_attr(item, "name"), tags=_labels(item, "labels"))

    def storage_buckets(self) -> Iterable[CloudResource]:
        for bucket in self._storage_client().list_buckets():
            yield CloudResource(
                identifier=_attr(bucket, "name"),
                location=_attr(bucket, "location"),
                tags=_labels(bucket, "labels"),
            )

    def service_accounts(self) -> Iterable[CloudResource]:
        request = {"name": f"projects/{self._project_id}"}
        response = self._iam_client().list_service_accounts(request=request)
        for account in _items(response, "accounts"):
            # No labels exist on this resource type; the email is the handle.
            yield CloudResource(identifier=_attr(account, "email"))

    def project_iam_bindings(self) -> Iterable[CloudResource]:
        if self._project_iam is None:
            raise RuntimeError(
                "project_iam_client is required to scan project IAM bindings; a scan "
                "that skips them cannot claim 'no dangling IAM/role/grant'"
            )
        policy = self._project_iam.get_project_iam_policy(project_id=self._project_id)
        for binding in policy.get("bindings", []):
            role = str(binding.get("role", ""))
            for member in binding.get("members", []):
                yield CloudResource(identifier=f"{role} -> {member}")


def _items(response: Any, key: str = "items") -> list[Any]:
    """Page items from a response that may be a proto, a dict, or an iterator."""
    if isinstance(response, dict):
        return list(response.get(key, []) or [])
    listed = getattr(response, key, None)
    if listed is not None:
        return list(listed)
    return list(response)
