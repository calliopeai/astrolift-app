"""Billing classes for the live certification campaign (#1417, decision 2).

Spend is gated by the *shape* of a variant's cost, not by a dollar ceiling. A
flat cap is the wrong instrument when the spread between the cheapest and
priciest variant is four orders of magnitude, and it invites gaming by
shortening TTL. Billing shape is harder to game and matches how the risk
actually distributes.

    A   No idle cost. Pay per request or per GB, nothing running.
    B   Small fixed floor. A key, a log group, an endpoint ENI.
    C   Hourly capacity floor. Something is always running and billing.
    D   Hourly floor plus provision or delete latency measured in hours.

A and B run freely. C needs a pre-flight estimate logged on the issue before
anything is created. D is not certified at all; those variants are reclassified
as experimental and documented as unsupported.

An unclassified variant is refused rather than assumed cheap. The failure this
guards against is a new entry landing in the matrix and being swept into a
"run the class A tranche" batch because nobody remembered to classify it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BillingClass(StrEnum):
    NO_IDLE_COST = "A"
    SMALL_FIXED_FLOOR = "B"
    HOURLY_CAPACITY_FLOOR = "C"
    HOURLY_FLOOR_SLOW_LIFECYCLE = "D"


#: Classes that may be certified without a logged spend estimate.
RUNS_FREELY = frozenset({BillingClass.NO_IDLE_COST, BillingClass.SMALL_FIXED_FLOOR})

#: Classes that are never certified, per decision 4.
NEVER_CERTIFIED = frozenset({BillingClass.HOURLY_FLOOR_SLOW_LIFECYCLE})


class UnclassifiedVariant(LookupError):
    """A (kind, variant) with no billing class.

    Refused rather than defaulted. A new matrix entry with no classification
    must not be swept into a class A batch by an operator running "the free
    tranche".
    """


class NotCertifiable(RuntimeError):
    """The variant is class D: real money and hours of wall clock, for
    something with no evidence of demand."""


class EstimateRequired(RuntimeError):
    """A class C variant was asked to run without a logged cost estimate."""


@dataclass(frozen=True)
class Classification:
    billing_class: BillingClass
    rationale: str

    @property
    def runs_freely(self) -> bool:
        return self.billing_class in RUNS_FREELY


def _c(kind: str, variant: str) -> tuple[str, str]:
    return (kind, variant)


# AWS. The class C membership follows decision 2's enumeration directly
# (Aurora, ElastiCache, MemoryDB, OpenSearch, Redshift, MSK, Neptune,
# DocumentDB, MQ, FSx) plus the RDS engines, which are the same shape.
_AWS: dict[tuple[str, str], Classification] = {
    # -- A: nothing runs when idle -------------------------------------------
    _c("event_bus", "eventbridge"): Classification(
        BillingClass.NO_IDLE_COST, "Billed per event published; an idle bus costs nothing."
    ),
    _c("topic", "sns_standard"): Classification(BillingClass.NO_IDLE_COST, "Billed per publish and per delivery."),
    _c("topic", "sns_fifo"): Classification(
        BillingClass.NO_IDLE_COST, "Billed per publish; FIFO ordering carries no idle charge."
    ),
    _c("workflow_engine", "step_functions_standard"): Classification(
        BillingClass.NO_IDLE_COST, "Billed per state transition."
    ),
    _c("workflow_engine", "step_functions_express"): Classification(
        BillingClass.NO_IDLE_COST, "Billed per request and duration."
    ),
    _c("api_gateway", "rest_api"): Classification(
        BillingClass.NO_IDLE_COST, "Billed per request; a deployed API with no traffic is free."
    ),
    _c("api_gateway", "websocket_api"): Classification(
        BillingClass.NO_IDLE_COST, "Billed per message and per connection-minute."
    ),
    _c("stream", "firehose"): Classification(
        BillingClass.NO_IDLE_COST, "Billed per GB ingested; an idle delivery stream costs nothing."
    ),
    _c("wide_column", "keyspaces"): Classification(
        BillingClass.NO_IDLE_COST,
        "On-demand capacity is per request. Certify on demand mode; provisioned mode is class C.",
    ),
    # SMS is per-message and has no idle floor, but unlike the rest of class A
    # a mistake here reaches real handsets and real per-message spend.
    _c("sms", "sns_sms"): Classification(
        BillingClass.NO_IDLE_COST,
        "Per message, no idle cost. Certify against the SMS sandbox: a loop that "
        "sends for real is the one class A failure that costs money and annoys people.",
    ),
    # -- B: a small fixed floor ----------------------------------------------
    _c("encryption_key", "kms"): Classification(
        BillingClass.SMALL_FIXED_FLOOR, "About a dollar per key per month, plus per request."
    ),
    _c("observability", "cloudwatch"): Classification(
        BillingClass.SMALL_FIXED_FLOOR, "A log group is free until written to; retention is per GB."
    ),
    _c("private_endpoint", "vpc_endpoint"): Classification(
        BillingClass.SMALL_FIXED_FLOOR, "An interface endpoint bills per ENI-hour, per AZ."
    ),
    _c("filesystem", "efs"): Classification(
        BillingClass.SMALL_FIXED_FLOOR,
        "Billed per GB stored with no provisioned floor, so an empty filesystem is "
        "near zero, but it is storage rather than nothing.",
    ),
    _c("stream", "kinesis"): Classification(
        BillingClass.SMALL_FIXED_FLOOR,
        "On-demand carries a per-stream hourly charge. Real, small, and bounded by "
        "a short TTL; not the capacity floor that defines class C.",
    ),
    # -- C: something is always running --------------------------------------
    **{
        _c(kind, variant): Classification(BillingClass.HOURLY_CAPACITY_FLOOR, rationale)
        for kind, variant, rationale in [
            ("cache", "elasticache_memcached", "Node-hours from creation."),
            ("cache", "elasticache_serverless_memcached", "Serverless still bills a minimum capacity floor."),
            ("redis", "elasticache_valkey", "Node-hours from creation."),
            ("redis", "elasticache_serverless_redis", "Serverless still bills a minimum capacity floor."),
            ("redis", "elasticache_serverless_valkey", "Serverless still bills a minimum capacity floor."),
            ("redis", "memorydb", "Node-hours, and it is durable so the floor is higher."),
            ("mysql", "aurora_mysql", "Instance-hours plus storage."),
            ("mysql", "aurora_mysql_serverless_v2", "ACU floor bills continuously, even at minimum."),
            ("postgres", "aurora_postgres", "Instance-hours plus storage."),
            ("postgres", "aurora_postgres_serverless_v2", "ACU floor bills continuously, even at minimum."),
            ("document_db", "documentdb", "Instance-hours plus storage."),
            ("document_db", "documentdb_serverless_v2", "ACU floor bills continuously."),
            ("graph_db", "neptune", "Instance-hours plus storage."),
            ("graph_db", "neptune_serverless", "NCU floor bills continuously."),
            ("event_stream", "msk", "Broker-hours plus storage."),
            ("event_stream", "msk_serverless", "Per-cluster hourly charge plus partition-hours."),
            ("mq", "amazon_mq_activemq", "Broker instance-hours."),
            ("mq", "amazon_mq_rabbitmq", "Broker instance-hours."),
            ("search", "opensearch_serverless", "OCU floor bills continuously and the minimum is not small."),
            ("vector_index", "opensearch_serverless_vector", "Same OCU floor as the search collection."),
            ("warehouse", "redshift", "Node-hours."),
            ("warehouse", "redshift_serverless", "RPU floor with a per-query minimum duration."),
            ("filesystem", "fsx_lustre", "Provisioned throughput and capacity bill from creation."),
            ("filesystem", "fsx_openzfs", "Provisioned throughput and capacity bill from creation."),
            ("filesystem", "fsx_windows", "Provisioned capacity plus a directory-service dependency."),
            ("database_proxy", "rds_proxy", "Per vCPU-hour of the database it fronts, and it needs one."),
            ("mssql", "rds_sqlserver_express", "Instance-hours; licence included but the instance runs."),
            ("mssql", "rds_sqlserver_web", "Instance-hours plus licence."),
            ("mssql", "rds_sqlserver_standard", "Instance-hours plus a substantial licence charge."),
        ]
    },
    # -- D: not certified -----------------------------------------------------
    _c("mssql", "rds_sqlserver_enterprise"): Classification(
        BillingClass.HOURLY_FLOOR_SLOW_LIFECYCLE,
        "Enterprise licence per hour, and no evidence of demand. Decision 4.",
    ),
}


# GCP.  The certification surface is intentionally small today, but it still
# needs a complete table: an absent provider table made the spend policy
# impossible to enforce on the real runner.
_GCP: dict[tuple[str, str], Classification] = {
    _c("email", "smtp"): Classification(
        BillingClass.NO_IDLE_COST,
        "The relay is external and billed per message; Astrolift provisions no idle capacity.",
    ),
    _c("workflow_engine", "workflows"): Classification(
        BillingClass.NO_IDLE_COST, "Billed per workflow step and external call."
    ),
    _c("cdn", "cloud_cdn"): Classification(
        BillingClass.NO_IDLE_COST,
        "Billed for requests and egress; an idle configuration has no capacity floor.",
    ),
    _c("encryption_key", "cloud_kms"): Classification(
        BillingClass.SMALL_FIXED_FLOOR, "A key version carries a small monthly floor plus operation charges."
    ),
    _c("private_endpoint", "private_service_connect"): Classification(
        BillingClass.SMALL_FIXED_FLOOR, "The forwarding rule and endpoint carry a bounded hourly charge."
    ),
    _c("observability", "cloud_operations"): Classification(
        BillingClass.SMALL_FIXED_FLOOR,
        "The workspace is near-zero idle; ingestion and retention are usage billed.",
    ),
}


# Azure.  Service names that contain "serverless" are not assumed free: SQL
# serverless retains a billed compute floor while provisioned, just as the AWS
# table treats Aurora Serverless v2.
_AZURE: dict[tuple[str, str], Classification] = {
    # The driver only admits standard SKUs, never provisioned throughput.
    # https://learn.microsoft.com/azure/ai-services/openai/how-to/deployment-types
    _c("model_endpoint", "azure_foundry"): Classification(
        BillingClass.NO_IDLE_COST, "Standard Foundry deployments bill per token, with no reserved capacity."
    ),
    **{
        _c(kind, variant): Classification(BillingClass.HOURLY_CAPACITY_FLOOR, rationale)
        for kind, variant, rationale in [
            (
                "mssql",
                "azure_sql_serverless",
                "Auto-pause can reduce spend, but provisioned compute has a billed floor.",
            ),
            ("mssql", "azure_sql_database", "Provisioned vCores bill from creation."),
            ("mssql", "azure_sql_hyperscale", "Provisioned compute and storage bill from creation."),
            ("redis", "azure_managed_redis", "A cache capacity bills by the hour from creation."),
            (
                "document_db",
                "cosmos_nosql",
                "The certification account provisions throughput with an hourly floor.",
            ),
            (
                "document_db",
                "cosmos_mongodb",
                "The certification account provisions throughput with an hourly floor.",
            ),
            (
                "graph_db",
                "cosmos_gremlin",
                "The certification account provisions throughput with an hourly floor.",
            ),
            (
                "wide_column",
                "cosmos_cassandra",
                "The certification account provisions throughput with an hourly floor.",
            ),
            (
                "kv_store",
                "cosmos_table",
                "The certification account provisions throughput with an hourly floor.",
            ),
            ("filesystem", "azure_files", "Provisioned share capacity and transactions can bill while idle."),
            (
                "filesystem",
                "azure_files_classic",
                "The backing storage account and share retain billable storage.",
            ),
            (
                "event_bus",
                "event_grid_namespace",
                "Namespace throughput units retain an hourly capacity floor.",
            ),
            ("stream", "event_hubs", "Throughput or processing units bill by the hour."),
            (
                "event_stream",
                "event_hubs_kafka",
                "The Event Hubs namespace bills throughput units by the hour.",
            ),
        ]
    },
    _c("event_bus", "event_grid"): Classification(
        BillingClass.NO_IDLE_COST, "Basic Event Grid is billed per operation."
    ),
    _c("faas", "azure_functions"): Classification(
        BillingClass.NO_IDLE_COST,
        "The certification shape uses consumption billing per execution and duration.",
    ),
    _c("encryption_key", "key_vault_key"): Classification(
        BillingClass.SMALL_FIXED_FLOOR,
        "Key Vault operations are usage billed and the key itself has a small floor.",
    ),
    _c("private_endpoint", "private_link"): Classification(
        BillingClass.SMALL_FIXED_FLOOR, "A private endpoint has a bounded endpoint-hour charge."
    ),
    _c("mssql", "azure_sql_managed_instance"): Classification(
        BillingClass.HOURLY_FLOOR_SLOW_LIFECYCLE,
        "Multi-hour provision and delete plus a substantial vCore floor. Decision 4.",
    ),
    _c("api_gateway", "api_management"): Classification(
        BillingClass.HOURLY_FLOOR_SLOW_LIFECYCLE,
        "APIM Developer has a material hourly floor and a slow lifecycle. Decision 4.",
    ),
}


CLASSIFICATIONS: dict[str, dict[tuple[str, str], Classification]] = {
    "aws": _AWS,
    "gcp": _GCP,
    "azure": _AZURE,
}


def classify(provider: str, kind: str, variant: str) -> Classification:
    """The billing class for one (kind, variant), or refuse.

    Refusing an unclassified variant is the point rather than an inconvenience:
    the alternative is a new matrix entry silently inheriting "runs freely".
    """
    table = CLASSIFICATIONS.get(provider)
    if table is None:
        raise UnclassifiedVariant(
            f"no billing classification table for provider {provider!r}; "
            f"classify its variants before certifying any of them"
        )
    try:
        return table[(kind, variant)]
    except KeyError:
        raise UnclassifiedVariant(
            f"{provider} {kind}:{variant} has no billing class. Add one to "
            f"providers/_cert/billing.py before certifying it; an unclassified "
            f"variant is not assumed free."
        ) from None


def check_may_run(
    provider: str,
    kind: str,
    variant: str,
    *,
    estimate_logged: bool = False,
) -> Classification:
    """Decide whether this variant may be provisioned now.

    Raises rather than returning a boolean: every caller of this must stop, and
    a boolean invites being ignored at one call site.
    """
    classification = classify(provider, kind, variant)

    if classification.billing_class in NEVER_CERTIFIED:
        raise NotCertifiable(
            f"{provider} {kind}:{variant} is billing class D and is not certified "
            f"(#1417 decision 4): {classification.rationale}"
        )

    if not classification.runs_freely and not estimate_logged:
        raise EstimateRequired(
            f"{provider} {kind}:{variant} is billing class "
            f"{classification.billing_class.value} ({classification.rationale}) and "
            f"needs a pre-flight cost estimate logged before it runs (#1417 decision 2). "
            f"Estimates come from providers/_sdk/cost.py CostEstimator against the live "
            f"pricing API, never a hard-coded table."
        )

    return classification
