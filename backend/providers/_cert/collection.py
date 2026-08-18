"""The certification manifest collection: what exists, and why a cell does not
(spec 43 §3.1).

The fixture repos exist; what did not exist is a declared set of manifests that
exercise buildout and teardown across the matrix. This module is the ledger for
the positive half of that set -- the happy path and the per-kind grid -- and the
renderer that turns it into the committed TOML under ``verification/manifests``.

Generated and committed, like ``docs/managed_service_coverage.md``: the ledger
is the review surface, the rendered tree is what a reviewer diffs, and
``tests/_cert/test_collection.py`` fails when they disagree. Sixty-odd
near-identical manifests written by hand drift within a week; the choices that
actually deserve review are the variant per cell and the cells that have none.

Two rules from the spec are enforced here rather than in prose.

*A manifest names a fixture repo from spec 42 and nothing bespoke.* Every cell
uses ``astrolift-sample-api``, which is the managed-service workhorse: its
``/debug`` reports which bindings reached the pod and its ``/selftest`` actually
connects to postgres, redis, the queue and object storage. Kinds it cannot
connect to are still covered for binding presence, which is what the fixture's
own ``exotic.toml`` variant does.

*A cell with no executable variant gets no invented manifest.* It goes in
:data:`NOT_EXPRESSIBLE` with the reason, and the guard test refuses an entry
there that the availability matrix says is executable after all.

The negative manifests are hand-written and live outside this ledger: each one
encodes a different refusal, and generating them from a table would hide the
one thing worth reading in each.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from _cert.campaign import CAMPAIGN_TAG_KEY, Campaign
from _sdk.availability import MATRIX
from _sdk.coverage import CLOUDS, EXECUTABLE_STATUSES, OPT_IN_TIER
from _sdk.managed_service_kinds import KINDS

CAMPAIGN = Campaign("cert2026q3")
"""The 2026 Q3 tri-cloud certification pass. Every app in the collection is
named ``cert2026q3-<cell>``, which is how the orphan scanners find residue on
surfaces that carry no operator tag."""

FIXTURE_REPO = "calliopeai/astrolift-sample-api"
FIXTURE_IMAGE = "docker.io/calliopeai/astrolift-sample-api:main-df5ffa3"
"""Pinned, not ``latest``: campaign blocker B3 in spec 41 was a first deploy
that failed because the tag moved. Re-pin with the fixture repo's current
``main`` sha7 when the fixture changes; the tag scheme is the fixture's own
``main-<sha7>`` from its build workflow."""

HAPPY_PATH_SERVICES: tuple[tuple[str, str], ...] = (
    ("postgres", "records"),
    ("redis", "cache"),
    ("queue", "jobs"),
    ("object_store", "archive"),
)
"""The certification target: web app plus a database, a cache, a queue and
object storage. Binding names match the fixture's own ``full.toml`` so
``/selftest`` exercises each one for real."""

VERIFICATION_ROOT = Path(__file__).resolve().parents[3] / "verification"
MANIFEST_ROOT = VERIFICATION_ROOT / "manifests"


@dataclass(frozen=True)
class Cell:
    """One (kind, cloud) square of the per-kind grid."""

    kind: str
    cloud: str
    variant: str

    @property
    def app_name(self) -> str:
        return f"{CAMPAIGN.slug}-{self.kind.replace('_', '-')}-{self.cloud}"

    @property
    def path(self) -> Path:
        return MANIFEST_ROOT / "per-kind" / self.kind / f"{self.cloud}.toml"


#: The variant each per-kind cell provisions. Where a cloud ships several, the
#: choice is the smallest viable one that still exercises the driver, per spec
#: 43 §4.3: the campaign proves lifecycle, not performance.
#:
#: Deliberate picks worth naming: ``event_stream`` takes MSK Serverless over a
#: provisioned cluster, ``graph_db`` takes Neptune Serverless, and ``mssql``
#: takes SQL Server Express on AWS and the serverless Azure SQL tier, because
#: the licensed tiers are the same driver at several times the hourly floor.
#: ``kv_store`` on Azure takes ``cosmos_table`` rather than ``cosmos`` so the
#: manifest names the API it actually books.
VARIANTS: dict[str, dict[str, str]] = {
    "cache": {"aws": "elasticache_memcached"},
    "document_db": {"aws": "documentdb", "gcp": "firestore_native", "azure": "cosmos_nosql"},
    "email": {"aws": "ses", "azure": "azure_acs"},
    "encryption_key": {"aws": "kms", "gcp": "cloud_kms", "azure": "key_vault_key"},
    "event_bus": {"aws": "eventbridge", "gcp": "eventarc", "azure": "event_grid"},
    "event_stream": {"aws": "msk_serverless", "gcp": "managed_kafka", "azure": "event_hubs_kafka"},
    "faas": {"aws": "lambda", "gcp": "cloud_functions_gen2", "azure": "azure_functions"},
    "filesystem": {"aws": "efs", "gcp": "filestore", "azure": "azure_files"},
    "graph_db": {"aws": "neptune_serverless", "gcp": "spanner_graph", "azure": "cosmos_gremlin"},
    "kv_store": {"aws": "dynamodb", "gcp": "bigtable", "azure": "cosmos_table"},
    "model_endpoint": {"aws": "bedrock", "gcp": "vertex_ai", "azure": "azure_openai"},
    "mssql": {
        "aws": "rds_sqlserver_express",
        "gcp": "cloudsql_sqlserver",
        "azure": "azure_sql_serverless",
    },
    "mysql": {"aws": "rds_mysql", "gcp": "cloudsql", "azure": "azure_mysql_flex"},
    "object_store": {"aws": "s3", "gcp": "gcs", "azure": "azure_blob"},
    "observability": {"aws": "cloudwatch", "gcp": "cloud_operations"},
    "postgres": {"aws": "rds", "gcp": "cloudsql", "azure": "azure_pg_flex"},
    "private_endpoint": {
        "aws": "vpc_endpoint",
        "gcp": "private_service_connect",
        "azure": "private_link",
    },
    "queue": {"aws": "sqs", "gcp": "pubsub", "azure": "azure_servicebus"},
    "redis": {"aws": "elasticache", "gcp": "memorystore", "azure": "azure_cache_redis"},
    "search": {"aws": "opensearch", "azure": "azure_ai_search_fulltext"},
    "topic": {"aws": "sns_standard", "gcp": "pubsub_topic", "azure": "service_bus_topic"},
    "vector_index": {
        "aws": "opensearch_vector",
        "gcp": "vertex_matching_engine",
        "azure": "azure_ai_search_vector",
    },
    "workflow_engine": {"aws": "step_functions_standard", "gcp": "workflows"},
}

#: Cells with no manifest, and why. Inventing one would mean naming a variant
#: the platform cannot provision, which fails at the driver lookup and teaches
#: nobody anything.
#:
#: The last three are the interesting ones. Each has an in-cluster variant, so
#: spec 43's exit criterion ("executable on all three clouds *or* an in-cluster
#: variant") is arguably met -- but a manifest cannot say so. Driver lookup is
#: ``plugins.get(<cluster plugin>, "managed:<kind>:<variant>")``, and the Azure
#: plugin registers no ``k8s_native`` drivers, so an AKS-hosted app cannot book
#: the in-cluster variant that covers its gap. That is a real hole in the
#: portability story and it needs a decision before the grid can claim those
#: three cells either way.
NOT_EXPRESSIBLE: dict[tuple[str, str], str] = {
    ("cache", "gcp"): "no managed variant; declared gap, in-cluster memcached is astrolift-app#1465",
    ("cache", "azure"): "no managed variant; declared gap, in-cluster memcached is astrolift-app#1465",
    ("email", "gcp"): "gcp_thirdparty is status planned; declared gap astrolift-app#1453",
    ("observability", "azure"): (
        "azure_monitor is status planned; kube_prometheus_stack is k8s_native only "
        "and the azure plugin registers no in-cluster drivers"
    ),
    ("search", "gcp"): (
        "gcp_elastic_cloud is status planned; opensearch_operator is k8s_native only "
        "and the gcp plugin registers no in-cluster drivers"
    ),
    ("workflow_engine", "azure"): (
        "logic_apps is status planned; argo_workflows is k8s_native only "
        "and the azure plugin registers no in-cluster drivers"
    ),
}


def default_tier_kinds() -> tuple[str, ...]:
    """Every managed-service kind the campaign covers, matrix-sorted.

    Read from the matrix rather than listed here so a kind added to the
    catalogue shows up as a missing manifest instead of being silently outside
    the grid. Opt-in kinds are excluded: spec 43 scopes the campaign to the
    surface Astrolift guarantees across clouds.
    """
    return tuple(sorted({entry.kind for entry in MATRIX.managed_services} - OPT_IN_TIER))


def executable_variants(kind: str, cloud: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            entry.variant
            for entry in MATRIX.managed_services
            if entry.kind == kind and entry.plugin_id == cloud and entry.status in EXECUTABLE_STATUSES
        )
    )


def binding_envs(cell: Cell) -> tuple[str, ...]:
    """What VERIFY-UP asserts reached the pod for this cell.

    The variant's own declaration wins when it has one, because a variant can
    emit provider-native variables on top of the portable envelope. Otherwise
    the kind catalogue's required envelope is the contract every variant of the
    kind must satisfy, which is still an assertion worth writing down.
    """
    entry = next(
        (
            e
            for e in MATRIX.managed_services
            if e.kind == cell.kind and e.plugin_id == cell.cloud and e.variant == cell.variant
        ),
        None,
    )
    if entry is not None and entry.binding_envs:
        return tuple(entry.binding_envs)
    kind = KINDS.get(cell.kind)
    return tuple(kind.binding_envs_required) if kind else ()


def cells() -> tuple[Cell, ...]:
    return tuple(
        Cell(kind=kind, cloud=cloud, variant=VARIANTS[kind][cloud])
        for kind in default_tier_kinds()
        for cloud in CLOUDS
        if cloud in VARIANTS.get(kind, {})
    )


# ---- rendering ---------------------------------------------------------------


def _campaign_block(*, cell: str, cloud: str, purpose: str) -> list[str]:
    return [
        "[campaign]",
        f'slug = "{CAMPAIGN.slug}"',
        f'cell = "{cell}"',
        f'cloud = "{cloud}"',
        f'fixture = "{FIXTURE_REPO}"',
        f'purpose = "{purpose}"',
        "",
        "# Operator tags the harness threads into ProvisionSpec.tags. The app name",
        "# carries the campaign slug as well, because no operator tag reaches an IAM",
        "# principal and two GCP drivers drop spec.tags outright -- see _cert/campaign.py.",
        "[campaign.tags]",
        f'{CAMPAIGN_TAG_KEY} = "{CAMPAIGN.slug}"',
        "",
    ]


def _web_workload(*, replicas: int = 1) -> list[str]:
    return [
        "[[workloads]]",
        'name = "web"',
        'kind = "deployment"',
        "is_public = true",
        f"replicas = {replicas}",
        'cpu_request = "100m"',
        'cpu_limit = "500m"',
        'memory_request = "128Mi"',
        'memory_limit = "256Mi"',
        "",
        "  [[workloads.containers]]",
        '  name = "app"',
        "  is_primary = true",
        f'  image_ref = "{FIXTURE_IMAGE}"',
        "  port = 8080",
        '  command = ["python", "entrypoint.py", "web"]',
        "",
        "    [workloads.containers.env]",
        '    LOG_LEVEL = "info"',
        '    PORT = "8080"',
        "",
        "    [workloads.containers.healthcheck]",
        '    kind = "http"',
        '    value = "/health"',
        "    port = 8080",
        "",
    ]


def _worker_workload() -> list[str]:
    return [
        "[[workloads]]",
        'name = "worker"',
        'kind = "deployment"',
        "replicas = 1",
        'cpu_request = "100m"',
        'cpu_limit = "500m"',
        'memory_request = "128Mi"',
        'memory_limit = "256Mi"',
        "",
        "  [[workloads.containers]]",
        '  name = "worker"',
        "  is_primary = true",
        f'  image_ref = "{FIXTURE_IMAGE}"',
        "  port = 0",
        '  command = ["python", "entrypoint.py", "worker"]',
        "",
        "    [workloads.containers.env]",
        '    LOG_LEVEL = "info"',
        "",
    ]


def _service(*, kind: str, name: str, variant: str) -> list[str]:
    return [
        "[[managed_services]]",
        f'kind = "{kind}"',
        f'name = "{name}"',
        f'variant = "{variant}"',
        "",
    ]


def render_happy_path(cloud: str) -> str:
    """The certification target for one cloud: web + worker + four services."""
    variants = {kind: VARIANTS[kind][cloud] for kind, _ in HAPPY_PATH_SERVICES}
    booked = ", ".join(f"{kind}:{variant}" for kind, variant in variants.items())
    lines = [
        f"# HAPPY PATH / {cloud} -- the certification target (spec 43 §1).",
        "#",
        "# Deploy a web app with a database, a cache, a queue and object storage;",
        "# update it; tear it down. GREEN twice, unattended, on all three clouds is",
        "# what certifies the beta. Everything else in this collection is coverage.",
        "#",
        f"# Books {booked}.",
        "# VERIFY-UP is the fixture's /selftest: it opens a real client against each",
        "# of the four, so a binding that reached the pod but does not work is red.",
        "#",
        "# Generated from providers/_cert/collection.py -- do not hand-edit.",
        f'name = "{CAMPAIGN.slug}-happy-{cloud}"',
        "",
        *_campaign_block(
            cell=f"happy-path/{cloud}",
            cloud=cloud,
            purpose="certification target: full lifecycle over four managed services",
        ),
        *_web_workload(replicas=2),
        *_worker_workload(),
    ]
    for kind, name in HAPPY_PATH_SERVICES:
        lines += _service(kind=kind, name=name, variant=variants[kind])
    return "\n".join(lines).rstrip() + "\n"


def render_per_kind(cell: Cell) -> str:
    """One kind, one cloud, one binding: the coverage grid's atom."""
    envs = binding_envs(cell)
    binding_line = (
        f"# Binding envelope to assert in /debug: {', '.join(envs)}."
        if envs
        else "# The catalogue declares no binding envelope for this kind: the cell proves "
        "provision,\n# readiness and clean teardown, not env injection."
    )
    lines = [
        f"# PER-KIND / {cell.kind} / {cell.cloud} -- variant {cell.variant}.",
        "#",
        "# One managed service, one cloud, the full lifecycle cycle. The fixture only",
        "# opens live clients for postgres, redis, queue and object_store; every other",
        "# kind is verified by binding presence in /debug plus a clean VERIFY-CLEAN.",
        f"{binding_line}",
        "#",
        "# Generated from providers/_cert/collection.py -- do not hand-edit.",
        f'name = "{cell.app_name}"',
        "",
        *_campaign_block(
            cell=f"per-kind/{cell.kind}/{cell.cloud}",
            cloud=cell.cloud,
            purpose=f"coverage: {cell.kind} via {cell.variant}",
        ),
        *_web_workload(),
        *_service(kind=cell.kind, name=cell.kind.replace("_", "-"), variant=cell.variant),
    ]
    return "\n".join(lines).rstrip() + "\n"


def rendered() -> dict[Path, str]:
    """Every generated manifest, keyed by the path it is committed at."""
    out: dict[Path, str] = {}
    for cloud in CLOUDS:
        out[MANIFEST_ROOT / "happy-path" / f"{cloud}.toml"] = render_happy_path(cloud)
    for cell in cells():
        out[cell.path] = render_per_kind(cell)
    return out


def write() -> list[Path]:
    written = []
    for path, text in rendered().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    written = write()
    print(f"wrote {len(written)} manifests under {MANIFEST_ROOT.name}/")


if __name__ == "__main__":
    main()
