"""GraphQL types for semantic layers (Mimwell brains): discovery (#2320) and
management (#2424)."""

from __future__ import annotations

import enum

import strawberry

from astrolift_graphql import GUID
from astrolift_graphql.errors import MutationErrorType


@strawberry.type(name="AstroliftBrainEnvironment")
class BrainEnvironmentType:
    name: str
    url: str


@strawberry.type(name="AstroliftBrainShare")
class BrainShareType:
    team: str
    access: str


@strawberry.type(name="AstroliftBrainEnvironmentState")
class BrainEnvironmentStateType:
    """One environment of a brain, served or not (#2444)."""

    name: str
    url: str
    served: bool = strawberry.field(description="Whether the environment has a URL.")
    paused: bool = strawberry.field(description="Whether its ingress is paused (the edge answers 503).")
    deployment_status: str | None = strawberry.field(
        description="The environment's latest deployment's status; null when it has none."
    )
    deployed_at: str | None


@strawberry.type(name="AstroliftBrainAgentShare")
class BrainAgentShareType:
    """An agent the brain is shared with (#2449), viewer only: its runs
    nobody launched read the brain as the agent when it also lists it."""

    agent: str
    app: str
    granted_at: str


@strawberry.type(name="AstroliftBrainHostedApp")
class BrainHostedAppType:
    """An app hosted on the brain (#2243), one row per environment of it."""

    slug: str
    environment: str


@strawberry.enum(name="BrainAuthority", description="Where canon lives (Mimwell substrate matrix).")
class BrainAuthority(enum.Enum):
    FILES = "files"
    SQLITE = "sqlite"
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MONGO = "mongo"
    D1 = "d1"


@strawberry.enum(name="BrainBlobs", description="Where blobs live (Mimwell substrate matrix).")
class BrainBlobs(enum.Enum):
    FS = "fs"
    DB_BLOB = "db-blob"
    S3 = "s3"
    AZURE_BLOB = "azure-blob"


@strawberry.enum(name="BrainVectors", description="Where the vector index lives (Mimwell substrate matrix).")
class BrainVectors(enum.Enum):
    LOCAL = "local"
    IN_DB = "in-db"
    EDGE_DB = "edge-db"
    EXTERNAL = "external"


@strawberry.enum(
    name="BrainGraph", description="Where the graph projection lives (Mimwell substrate matrix)."
)
class BrainGraph(enum.Enum):
    EDGES_TABLE = "edges-table"
    AGE = "age"


@strawberry.enum(
    name="BrainPersistence", description="Mimwell deploy.persistence; D1 and KV are the Worker path's."
)
class BrainPersistence(enum.Enum):
    NONE = "none"
    SQLITE_VOLUME = "sqlite-volume"
    EFS = "efs"
    POSTGRES = "postgres"
    S3 = "s3"
    D1 = "d1"
    KV = "kv"


@strawberry.input(name="BrainSubstrateInput")
class BrainSubstrateInput:
    """A Mimwell cell plus what the host attaches. Omitted placements are the
    files cell's (fs, local, edges-table); ``image`` deploys a given image
    instead of building the edition's portal Dockerfile."""

    authority: BrainAuthority = BrainAuthority.FILES
    blobs: BrainBlobs = BrainBlobs.FS
    vectors: BrainVectors = BrainVectors.LOCAL
    graph: BrainGraph = BrainGraph.EDGES_TABLE
    persistence: list[BrainPersistence] = strawberry.field(default_factory=list)
    replicas: int = 1
    image: str | None = None
    port: int = 8080


def selection_from_input(data: BrainSubstrateInput | None, *, keep_image: str = ""):
    from astrolift_registry.brain_substrate import Selection

    if data is None:
        return Selection(image=keep_image)
    return Selection(
        authority=data.authority.value,
        blobs=data.blobs.value,
        vectors=data.vectors.value,
        graph=data.graph.value,
        persistence=tuple(p.value for p in data.persistence),
        replicas=data.replicas,
        image=(data.image if data.image is not None else keep_image).strip(),
        port=data.port,
    )


@strawberry.type(name="AstroliftBrainResource")
class BrainResourceType:
    """One thing the cell asks the platform for: ``postgres`` (its managed
    service state), ``sqlite-volume`` / ``efs`` (declared claims) or ``s3``
    (``unbound``: Astrolift has no bucket binding)."""

    kind: str
    name: str
    environment: str
    state: str
    detail: str


@strawberry.type(name="AstroliftBrainSubstrate")
class BrainSubstrateType:
    authority: str
    blobs: str
    vectors: str
    graph: str
    cell: str = strawberry.field(description="The cell, as Mimwell names it.")
    persistence: list[str]
    replicas: int
    image: str
    adopted: bool | None = strawberry.field(
        description=(
            "Whether the brain has adopted a store authority (brain.authority at the running commit); "
            "null when it could not be read. A non-files cell is in effect once adopted."
        )
    )
    adopted_store: str
    graduation_needed: bool | None = strawberry.field(
        description="app/brain-manifest.json substrate.graduation.needed at the running commit."
    )
    resources: list[BrainResourceType]
    port: int = strawberry.field(description="The port the brain serves on (#2444).")


def substrate_to_type(row: dict) -> BrainSubstrateType:
    return BrainSubstrateType(
        **{
            k: row[k]
            for k in (
                "authority",
                "blobs",
                "vectors",
                "graph",
                "cell",
                "replicas",
                "image",
                "adopted",
                "port",
            )
        },
        persistence=list(row["persistence"]),
        adopted_store=row["adopted_store"],
        graduation_needed=row["graduation_needed"],
        resources=[BrainResourceType(**r) for r in row["resources"]],
    )


@strawberry.type(name="AstroliftBrainSummary")
class BrainSummaryType:
    """A brain's health and freshness without reading its source host
    (#2444): the latest deployment in ``environmentName`` now, and what the
    last ``astroliftBrainStatus`` read there found."""

    environment_name: str
    deployment: BrainDeploymentStateType | None
    compiled_at: str | None
    behind: bool | None
    last_ingest: BrainIngestRunType | None
    checked_at: str | None = strawberry.field(
        description=(
            "When compiledAt, behind and lastIngest were read from the source host; null when they "
            "never were (or the reading expired), and they are null too."
        )
    )


@strawberry.type(name="AstroliftBrain")
class BrainType:
    """A hosted brain the caller can read (#2320): an app of a brain kind."""

    id: GUID
    slug: str
    name: str
    kind: str
    team: str
    project: str
    environments: list[BrainEnvironmentType] = strawberry.field(
        description="Served environments (with a URL), the ones a client connects to."
    )
    shared_with: list[BrainShareType]
    archived: bool = strawberry.field(description="Archived: listed only with includeArchived; restoreApp.")
    all_environments: list[BrainEnvironmentStateType] = strawberry.field(
        description="Every environment, served or not, with its state (#2444)."
    )
    summary: BrainSummaryType | None = strawberry.field(
        description="Health and freshness for lists, without host reads (#2444); null with no environment."
    )
    hosted_apps: list[BrainHostedAppType] | None = strawberry.field(
        default=None,
        description=(
            "Apps hosted on this brain that the caller can read. Filled by astroliftBrain; null in lists."
        ),
    )
    substrate: BrainSubstrateType | None = strawberry.field(
        default=None,
        description=(
            "The cell last applied and the resources it needs, without host reads (adopted and "
            "graduationNeeded are null here; astroliftBrainStatus reads them). Null in lists."
        ),
    )
    agent_shares: list[BrainAgentShareType] | None = strawberry.field(
        default=None,
        description="Agents the brain is shared with that the caller can read (#2449). Null in lists.",
    )


def brain_to_type(app, *, hosted: bool = False, overview: dict | None = None) -> BrainType:
    """``overview`` is the brain's entry of ``brain_manage.overviews``; a
    list passes it, so its rows share three queries."""
    from astrolift_registry.brain_manage import overviews
    from astrolift_registry.brains import describe_brain

    row = describe_brain(app)
    overview = overview if overview is not None else overviews([app])[app.pk]
    summary = overview["summary"]
    return BrainType(
        id=GUID(row["id"]),
        slug=row["slug"],
        name=row["name"],
        kind=row["kind"],
        team=row["team"],
        project=row["project"],
        environments=[BrainEnvironmentType(**e) for e in row["environments"]],
        shared_with=[BrainShareType(**x) for x in row["shared_with"]],
        archived=app.archived_at is not None,
        all_environments=[BrainEnvironmentStateType(**e) for e in overview["environments"]],
        summary=_summary_to_type(summary) if summary is not None else None,
        hosted_apps=hosted_apps(app) if hosted else None,
        substrate=_local_substrate(app) if hosted else None,
        agent_shares=agent_shares(app) if hosted else None,
    )


def brains_to_types(apps) -> list[BrainType]:
    from astrolift_registry.brain_manage import overviews

    apps = list(apps)
    found = overviews(apps)
    return [brain_to_type(app, overview=found[app.pk]) for app in apps]


def _summary_to_type(row: dict) -> BrainSummaryType:
    deployment = row["deployment"]
    ingest = row["last_ingest"]
    return BrainSummaryType(
        environment_name=row["environment_name"],
        deployment=BrainDeploymentStateType(**{**deployment, "id": GUID(deployment["id"])})
        if deployment
        else None,
        compiled_at=row["compiled_at"],
        behind=row["behind"],
        last_ingest=BrainIngestRunType(**ingest) if ingest else None,
        checked_at=row["checked_at"],
    )


def agent_shares(brain) -> list[BrainAgentShareType]:
    """Live shares of ``brain`` with agents the caller can read."""
    from astrolift_agents.visibility import agent_workloads
    from astrolift_registry.models import BrainAgentGrant

    rows = (
        BrainAgentGrant.objects.filter(
            brain=brain,
            organization_id=brain.organization_id,
            deleted_at__isnull=True,
            agent__in=agent_workloads(brain.organization_id).values("pk"),
        )
        .select_related("agent__registered_app")
        .order_by("agent__slug", "pk")
    )
    return [
        BrainAgentShareType(
            agent=row.agent.slug, app=row.agent.registered_app.slug, granted_at=row.created_at.isoformat()
        )
        for row in rows
    ]


def _local_substrate(app) -> BrainSubstrateType:
    from astrolift_registry.brain_manage import substrate_status

    return substrate_to_type(substrate_status(app, deployed_commit="", connection=None, warnings=[]))


def hosted_apps(brain) -> list[BrainHostedAppType]:
    """Live apps bound to ``brain`` that the caller can read, two queries."""
    from django.db.models import Prefetch

    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.brain_apps import live_bindings
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.visibility import visible_registry_apps
    from core.permissions import Permission

    bound = live_bindings().filter(brain=brain, organization_id=brain.organization_id)
    apps = (
        visible_registry_apps(
            RegisteredApp.objects.filter(
                pk__in=bound.values("registered_app_id"), organization_id=brain.organization_id
            ),
            Permission.APP_READ,
        )
        .prefetch_related(
            Prefetch(
                "environments",
                queryset=AppEnvironment.objects.filter(deleted_at__isnull=True).order_by("name"),
                to_attr="live_envs",
            )
        )
        .order_by("slug")
    )
    out = []
    for app in apps:
        envs = [env.name for env in app.live_envs] or [""]
        out.extend(BrainHostedAppType(slug=app.slug, environment=name) for name in envs)
    return out


@strawberry.type(name="AstroliftBrainDeploymentState")
class BrainDeploymentStateType:
    id: GUID
    status: str
    image_tag: str
    commit_sha: str
    created_at: str | None


@strawberry.type(name="AstroliftBrainSourceHead")
class BrainSourceHeadType:
    branch: str
    commit_sha: str
    committed_at: str | None


@strawberry.type(name="AstroliftBrainIngestRun")
class BrainIngestRunType:
    status: str
    conclusion: str
    url: str
    created_at: str


@strawberry.type(name="AstroliftBrainStatus")
class BrainStatusType:
    """How fresh a brain is in one environment (#2424). Host reads that
    fail leave their field null and say why in ``warnings``."""

    slug: str
    environment_name: str
    url: str
    served: bool = strawberry.field(description="Whether any environment of the brain has a URL.")
    deployment: BrainDeploymentStateType | None = strawberry.field(
        description="The environment's latest deployment, whatever its state."
    )
    deployed_commit: str = strawberry.field(
        description="The commit of the environment's running deployment; empty when none runs."
    )
    source_head: BrainSourceHeadType | None
    behind: bool | None = strawberry.field(
        description="Whether the running commit differs from the deploy branch head; null when unknown."
    )
    compiled_at: str | None = strawberry.field(
        description="app/brain-manifest.json generated, read at the running commit; null when uncompiled."
    )
    last_ingest: BrainIngestRunType | None = strawberry.field(
        description="The newest knowledge-refresh.yml run; null when the repository has none."
    )
    substrate: BrainSubstrateType
    warnings: list[str]


def brain_status_to_type(slug: str, row: dict) -> BrainStatusType:
    deployment = row["deployment"]
    head = row["source_head"]
    ingest = row["last_ingest"]
    return BrainStatusType(
        slug=slug,
        environment_name=row["environment_name"],
        url=row["url"],
        served=row["served"],
        deployment=BrainDeploymentStateType(**{**deployment, "id": GUID(deployment["id"])})
        if deployment
        else None,
        deployed_commit=row["deployed_commit"],
        source_head=BrainSourceHeadType(**head) if head else None,
        behind=row["behind"],
        compiled_at=row["compiled_at"],
        last_ingest=BrainIngestRunType(**ingest) if ingest else None,
        substrate=substrate_to_type(row["substrate"]),
        warnings=list(row["warnings"]),
    )


@strawberry.enum(name="BrainCreationState")
class BrainCreationState(enum.Enum):
    PENDING = "pending"
    REPO_READY = "repo_ready"
    SCAFFOLDED = "scaffolded"
    REGISTERED = "registered"
    DEPLOYING = "deploying"
    READY = "ready"
    FAILED = "failed"


@strawberry.enum(name="RepoVisibility")
class RepoVisibility(enum.Enum):
    PRIVATE = "private"
    INTERNAL = "internal"
    PUBLIC = "public"


@strawberry.enum(name="BrainShareAccess")
class BrainShareAccess(enum.Enum):
    """``VIEWER`` reads the brain; ``DEPLOYER`` also edits and deploys it.
    Owners are managed on the app (grantTeamAccessToApp), never here."""

    VIEWER = "viewer"
    DEPLOYER = "deployer"


@strawberry.type(name="AstroliftBrainCreation")
class BrainCreationType:
    id: GUID
    slug: str
    state: BrainCreationState
    app: BrainType | None = strawberry.field(
        description="The brain once registered and readable by the caller."
    )
    repo: str
    template_ref: str
    commit_sha: str | None
    deployment_id: GUID | None
    error: MutationErrorType | None


def creation_to_type(row) -> BrainCreationType:
    from astrolift_registry.brain_manage import find_brain

    app = find_brain(row.registered_app.slug) if row.registered_app_id and row.registered_app else None
    return BrainCreationType(
        id=GUID(str(row.guid)),
        slug=row.slug,
        state=BrainCreationState(row.state),
        app=brain_to_type(app) if app is not None else None,
        repo=row.repo,
        template_ref=row.template_ref,
        commit_sha=row.commit_sha or None,
        deployment_id=GUID(str(row.deployment.guid)) if row.deployment_id and row.deployment else None,
        error=MutationErrorType(code=row.error_code, message=row.error_message) if row.error_code else None,
    )


@strawberry.type(name="AstroliftBrainRebuild")
class BrainRebuildType:
    """What a rebuild started: a deployment, a CI run, or the ingest workflow
    (whose commit then deploys through the normal push path)."""

    slug: str
    mode: str
    deployment_id: GUID | None
    run_url: str | None
    ingest_run_url: str | None
    rebuilds: list[str] = strawberry.field(
        default_factory=list, description="What the deploy rebuilds, from the brain's substrate."
    )


@strawberry.enum(name="AstroliftBrainSort")
class BrainSort(enum.Enum):
    NEWEST = "newest"
    OLDEST = "oldest"
    NAME_ASC = "name_asc"
    NAME_DESC = "name_desc"


@strawberry.type(name="AstroliftBrainCell")
class BrainCellType:
    """One cell of Mimwell's substrate matrix and whether Astrolift can host it."""

    authority: str
    blobs: str
    vectors: str
    graph: str
    status: str = strawberry.field(description="supported, planned or unsupported.")
    reason: str


@strawberry.type(name="AstroliftBrainEdition")
class BrainEditionType:
    """What the Mimwell edition a new semantic layer starts from offers (#2444)."""

    template_repo: str
    ref: str = strawberry.field(description="The ref asked for, or the one this install pins.")
    version: str | None
    supports_app_slug: bool | None = strawberry.field(
        description=(
            "Whether the edition has deploy.app_slug, which a slug not ending in -brain needs; null "
            "when the edition could not be read (error says why)."
        )
    )
    ships_portal_container: bool | None = strawberry.field(
        description="Whether the edition ships the portal container a brain with no image builds."
    )
    error: str | None


def edition_to_type(ref: str) -> BrainEditionType:
    from astrolift_registry import brain_template

    try:
        info, error = brain_template.edition_info(brain_template.validate_ref(ref)), None
    except brain_template.TemplateError as exc:
        info, error = {}, exc.message
    return BrainEditionType(
        template_repo=brain_template.template_repo(),
        ref=ref,
        version=info.get("version") or None,
        supports_app_slug=info.get("supports_app_slug"),
        ships_portal_container=info.get("ships_portal_container"),
        error=error,
    )


def cells_to_types() -> list[BrainCellType]:
    """Every cell of Mimwell's matrix with its status, in axis order."""
    import itertools

    from astrolift_registry import brain_substrate

    out = []
    for values in itertools.product(*brain_substrate.AXES.values()):
        cell = dict(zip(brain_substrate.AXES, values, strict=True))
        status, reason = brain_substrate.cell_status(cell)
        out.append(BrainCellType(**cell, status=status, reason=reason))
    return out
