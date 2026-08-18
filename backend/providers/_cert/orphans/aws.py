"""AWS orphan scanner: RDS, ElastiCache, SQS, SNS, S3, IAM (spec 43 §3.2).

The hole the GCP and Azure scanners were written around. Spec 43 described this
one as already existing and it did not, which is also why AWS is not yet the
known-good control Phase 3 opens against.

Families are split wherever one API call cannot see the whole family, because a
family nobody queries is a family that leaks silently.

*RDS is two families.* ``describe_db_instances`` does not return clusters, so an
Aurora, DocumentDB or Neptune cluster whose members were deleted is invisible to
it while still billing for storage and backups. ``aurora.py``, ``documentdb.py``
and ``neptune.py`` all create clusters through the RDS control plane, so one
cluster family covers the three.

*ElastiCache is three families, one per API.* ``memcached_elasticache.py`` calls
``create_cache_cluster``, ``redis_elasticache.py`` calls
``create_replication_group`` and ``elasticache_serverless.py`` calls
``create_serverless_cache``. ``describe_cache_clusters`` sees only the first.
Reading one of the three and calling ElastiCache scanned is the exact shape of a
scan that lies.

*SNS subscriptions are deliberately not a family,* unlike Pub/Sub subscriptions
on GCP. ``DeleteTopic`` deletes the topic's subscriptions with it, so there is no
"subscription outlives its topic" residue to find. Restraint here is on purpose:
a family that can never fire trains people to skim the report.

*IAM carries no app tag.* Every role the platform creates
(``identity_irsa.py``, ``faas_lambda.py``, ``registry_ecr.py``,
``rds_proxy.py``) is stamped only with ``astrolift.io/managed-by`` or
``astrolift.io/managed``, never the app slug, so the deterministic role *name*
is the handle -- the same situation as a GCP service account. Inline role
policies are not a separate family because they are deleted with the role;
customer-managed policies are, even though no driver creates one today, because
"no driver creates one" is a claim about code that can rot and a scan that never
asks can never notice.

Known limit, stated so it stays a known one: ``aws/_naming.py::iam_role_name``
truncates at 63 characters and appends an 8-hex digest. A long
organization-plus-app prefix can push the campaign slug off the end of a role
name, and since the role carries no app tag either, such a role is invisible to
this scan. The campaign's app names are short enough that it does not bite here;
the fix if it ever does is an app tag on the role, not a looser name rule.

``AwsInventory`` is the seam. ``LiveAwsInventory`` builds each family from the
same boto3 clients the drivers use; tests drive the protocol with fakes, because
this is a scanner whose whole job is to be trusted when it says "clean".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from _cert.orphans.model import CloudResource, ScanReport, scan_families

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from _cert.campaign import Campaign

CLOUD = "aws"


class AwsInventory(Protocol):
    """Read-only listings the scan needs. One method per resource family."""

    def rds_instances(self) -> Iterable[CloudResource]: ...

    def rds_clusters(self) -> Iterable[CloudResource]: ...

    def elasticache_clusters(self) -> Iterable[CloudResource]: ...

    def elasticache_replication_groups(self) -> Iterable[CloudResource]: ...

    def elasticache_serverless_caches(self) -> Iterable[CloudResource]: ...

    def sqs_queues(self) -> Iterable[CloudResource]: ...

    def sns_topics(self) -> Iterable[CloudResource]: ...

    def s3_buckets(self) -> Iterable[CloudResource]: ...

    def iam_roles(self) -> Iterable[CloudResource]: ...

    def iam_policies(self) -> Iterable[CloudResource]: ...


def scan(inventory: AwsInventory, campaign: Campaign) -> ScanReport:
    """Everything in ``inventory`` that still belongs to ``campaign``.

    Call ``raise_if_dirty()`` on the result. Returning the report rather than
    raising here keeps a whole-campaign scan able to collect all three clouds
    before failing, but nothing may treat the report as advisory.
    """
    return scan_families(
        cloud=CLOUD,
        campaign=campaign,
        families=[
            ("rds_instance", inventory.rds_instances),
            ("rds_cluster", inventory.rds_clusters),
            ("elasticache_cluster", inventory.elasticache_clusters),
            ("elasticache_replication_group", inventory.elasticache_replication_groups),
            ("elasticache_serverless", inventory.elasticache_serverless_caches),
            ("sqs", inventory.sqs_queues),
            ("sns", inventory.sns_topics),
            ("s3", inventory.s3_buckets),
            ("iam_role", inventory.iam_roles),
            ("iam_policy", inventory.iam_policies),
        ],
    )


def tag_mapping(tags: Any) -> dict[str, str]:
    """Normalize AWS's several tag shapes into one mapping.

    AWS returns tags as a ``[{"Key": k, "Value": v}]`` list nearly everywhere,
    as ``[{"key": k, "value": v}]`` from a handful of newer APIs, and as a plain
    ``{k: v}`` dict from SQS. The scanner reads all three rather than picking
    one, because the alternative is a family that matches nothing and reports
    clean.
    """
    if not tags:
        return {}
    if isinstance(tags, dict):
        return {str(k): str(v) for k, v in tags.items()}
    mapping: dict[str, str] = {}
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        key = tag.get("Key", tag.get("key"))
        if key is None:
            continue
        mapping[str(key)] = str(tag.get("Value", tag.get("value", "")))
    return mapping


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    error = response.get("Error")
    return str(error.get("Code", "")) if isinstance(error, dict) else ""


def _tags_unless_absent(fetch: Any, absent_codes: Sequence[str]) -> dict[str, str]:
    """Tags for one resource, treating "this resource has no tags" as empty.

    Only the codes named by the caller are absorbed. Everything else -- a
    throttle, an access denial, a transient 5xx -- propagates, fails the family,
    and lands in the report as an unread family. Absorbing those would turn a
    permissions problem into a clean scan, which is the failure this whole
    module exists to prevent.
    """
    try:
        return tag_mapping(fetch())
    except Exception as exc:
        if _error_code(exc) in absent_codes:
            return {}
        raise


def _paginate(client: Any, operation: str, **kwargs: Any) -> Iterable[dict[str, Any]]:
    """Pages of one list/describe call, using boto3's paginator when there is
    one. A scan that reads only the first page under-reports on exactly the
    accounts that accumulate the most residue."""
    if client.can_paginate(operation):
        yield from client.get_paginator(operation).paginate(**kwargs)
        return
    yield getattr(client, operation)(**kwargs)


class LiveAwsInventory:
    """The credentialed adapter, built from the clients the drivers use.

    Untested against a live account -- Phase 2 is its first real run, by design:
    the campaign's whole shape is that nothing touches a cloud until the
    switch-on gate. The scan logic it feeds is covered offline with fakes.
    """

    def __init__(
        self,
        *,
        region: str,
        rds_client: Any | None = None,
        elasticache_client: Any | None = None,
        sqs_client: Any | None = None,
        sns_client: Any | None = None,
        s3_client: Any | None = None,
        iam_client: Any | None = None,
    ) -> None:
        self._region = region
        self._rds = rds_client
        self._elasticache = elasticache_client
        self._sqs = sqs_client
        self._sns = sns_client
        self._s3 = s3_client
        self._iam = iam_client

    def _client(self, attr: str, service: str) -> Any:
        existing = getattr(self, attr)
        if existing is None:
            import boto3

            existing = boto3.client(service, region_name=self._region)
            setattr(self, attr, existing)
        return existing

    # -- families ------------------------------------------------------------

    def rds_instances(self) -> Iterable[CloudResource]:
        rds = self._client("_rds", "rds")
        for page in _paginate(rds, "describe_db_instances"):
            for instance in page.get("DBInstances", []):
                yield CloudResource(
                    identifier=str(instance.get("DBInstanceIdentifier", "")),
                    location=str(instance.get("AvailabilityZone", "") or self._region),
                    tags=self._rds_tags(rds, instance, "DBInstanceArn"),
                )

    def rds_clusters(self) -> Iterable[CloudResource]:
        rds = self._client("_rds", "rds")
        for page in _paginate(rds, "describe_db_clusters"):
            for cluster in page.get("DBClusters", []):
                yield CloudResource(
                    identifier=str(cluster.get("DBClusterIdentifier", "")),
                    location=self._region,
                    tags=self._rds_tags(rds, cluster, "DBClusterArn"),
                )

    @staticmethod
    def _rds_tags(rds: Any, resource: dict[str, Any], arn_key: str) -> dict[str, str]:
        """``TagList`` inline when the API returned it, ``ListTagsForResource``
        when it did not. Both shapes are live: the inline list arrived across
        the RDS describe calls at different times and older endpoints omit it.
        """
        inline = resource.get("TagList")
        if inline is not None:
            return tag_mapping(inline)
        arn = resource.get(arn_key)
        if not arn:
            return {}
        return tag_mapping(rds.list_tags_for_resource(ResourceName=arn).get("TagList"))

    def elasticache_clusters(self) -> Iterable[CloudResource]:
        cache = self._client("_elasticache", "elasticache")
        for page in _paginate(cache, "describe_cache_clusters"):
            for cluster in page.get("CacheClusters", []):
                yield CloudResource(
                    identifier=str(cluster.get("CacheClusterId", "")),
                    location=str(cluster.get("PreferredAvailabilityZone", "") or self._region),
                    tags=self._elasticache_tags(cache, cluster.get("ARN")),
                )

    def elasticache_replication_groups(self) -> Iterable[CloudResource]:
        cache = self._client("_elasticache", "elasticache")
        for page in _paginate(cache, "describe_replication_groups"):
            for group in page.get("ReplicationGroups", []):
                yield CloudResource(
                    identifier=str(group.get("ReplicationGroupId", "")),
                    location=self._region,
                    tags=self._elasticache_tags(cache, group.get("ARN")),
                )

    def elasticache_serverless_caches(self) -> Iterable[CloudResource]:
        cache = self._client("_elasticache", "elasticache")
        for page in _paginate(cache, "describe_serverless_caches"):
            for serverless in page.get("ServerlessCaches", []):
                yield CloudResource(
                    identifier=str(serverless.get("ServerlessCacheName", "")),
                    location=self._region,
                    tags=self._elasticache_tags(cache, serverless.get("ARN")),
                )

    @staticmethod
    def _elasticache_tags(cache: Any, arn: Any) -> dict[str, str]:
        """ElastiCache returns no tags on any describe call, so every resource
        costs a second request. ``CacheClusterNotFound`` absorbs the race where
        the resource is deleted between the list and the tag read; anything else
        fails the family."""
        if not arn:
            return {}
        return _tags_unless_absent(
            lambda: cache.list_tags_for_resource(ResourceName=arn).get("TagList"),
            ("CacheClusterNotFound", "ReplicationGroupNotFoundFault", "ServerlessCacheNotFoundFault"),
        )

    def sqs_queues(self) -> Iterable[CloudResource]:
        sqs = self._client("_sqs", "sqs")
        for page in _paginate(sqs, "list_queues"):
            for url in page.get("QueueUrls", []) or []:
                yield CloudResource(
                    # The URL, not the bare name: it is what a human needs to
                    # act on the finding, and it still carries the name the
                    # campaign matches against.
                    identifier=str(url),
                    location=self._region,
                    tags=_tags_unless_absent(
                        lambda url=url: sqs.list_queue_tags(QueueUrl=url).get("Tags"),
                        ("AWS.SimpleQueueService.NonExistentQueue",),
                    ),
                )

    def sns_topics(self) -> Iterable[CloudResource]:
        sns = self._client("_sns", "sns")
        for page in _paginate(sns, "list_topics"):
            for topic in page.get("Topics", []):
                arn = topic.get("TopicArn")
                if not arn:
                    continue
                yield CloudResource(
                    identifier=str(arn),
                    location=self._region,
                    tags=_tags_unless_absent(
                        lambda arn=arn: sns.list_tags_for_resource(ResourceArn=arn).get("Tags"),
                        ("NotFound", "ResourceNotFound"),
                    ),
                )

    def s3_buckets(self) -> Iterable[CloudResource]:
        s3 = self._client("_s3", "s3")
        for bucket in s3.list_buckets().get("Buckets", []):
            name = str(bucket.get("Name", ""))
            if not name:
                continue
            yield CloudResource(
                identifier=name,
                location=self._bucket_region(s3, name),
                # An untagged bucket is normal and is still matched by name;
                # any other failure fails the family.
                tags=_tags_unless_absent(
                    lambda name=name: s3.get_bucket_tagging(Bucket=name).get("TagSet"),
                    ("NoSuchTagSet", "NoSuchTagSetError"),
                ),
            )

    @staticmethod
    def _bucket_region(s3: Any, name: str) -> str:
        """``ListBuckets`` is global and carries no region, so the report would
        otherwise name a bucket without saying where to look for it. ``None``
        from the API means us-east-1, which is the one place the AWS response
        encodes a value by omitting it."""
        location = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
        return str(location) if location else "us-east-1"

    def iam_roles(self) -> Iterable[CloudResource]:
        iam = self._client("_iam", "iam")
        for page in _paginate(iam, "list_roles"):
            for role in page.get("Roles", []):
                name = str(role.get("RoleName", ""))
                if not name:
                    continue
                yield CloudResource(
                    identifier=name,
                    # ``list_roles`` never returns tags, unlike most AWS list
                    # calls; the platform's roles carry only
                    # ``astrolift.io/managed-by`` anyway, so the name is the
                    # handle that actually matches.
                    tags=_tags_unless_absent(
                        lambda name=name: iam.list_role_tags(RoleName=name).get("Tags"),
                        ("NoSuchEntity",),
                    ),
                )

    def iam_policies(self) -> Iterable[CloudResource]:
        iam = self._client("_iam", "iam")
        # Scope=Local: AWS-managed policies are not ours and listing them would
        # bury any real finding in several hundred rows.
        for page in _paginate(iam, "list_policies", Scope="Local"):
            for policy in page.get("Policies", []):
                arn = policy.get("Arn")
                if not arn:
                    continue
                yield CloudResource(
                    identifier=str(policy.get("PolicyName", arn)),
                    tags=_tags_unless_absent(
                        lambda arn=arn: iam.list_policy_tags(PolicyArn=arn).get("Tags"),
                        ("NoSuchEntity",),
                    ),
                )
