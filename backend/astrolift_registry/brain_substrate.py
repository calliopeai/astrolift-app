"""A semantic layer's substrate, in Mimwell's own terms (#2424).

Mimwell describes where a brain keeps everything as a **cell** of four
placements (``docs/design/substrate-matrix.md``):

* authority: ``files`` | ``sqlite`` | ``postgres`` | ``mysql`` | ``mongo`` | ``d1``
* blobs: ``fs`` | ``db-blob`` | ``s3`` | ``azure-blob``
* vectors: ``local`` | ``in-db`` | ``edge-db`` | ``external``
* graph: ``edges-table`` | ``age``

and what a host attaches with ``deploy.persistence`` (``sqlite-volume``,
``efs``, ``postgres``, ``s3``; ``d1``, ``kv`` and ``none`` are the Worker
path's) and ``deploy.replicas``.

Astrolift does not invent a model of its own. ``cell_status`` is a port of
``knowledge_store.cell_status()`` (the one source of the statuses), and
``render_manifest`` is a port of ``scripts/gen-astrolift.py``'s mapping, with
``app_slug`` its naming of the app. They are pinned to Mimwell commits and held
to them by parity tests against the real function's table and the real
script's output; Astrolift never runs template code.

A brain takes any slug. One ending in ``-brain`` keeps Mimwell's default name,
``<client.slug>-brain``; any other is written to ``client.config.json`` as
``deploy.app_slug``, which the brain's Mimwell version must have.

Two things only the brain can do stay with the brain: adopting a non-files
authority (``knowledge-store.py`` cutover writes ``contract.json`` and
``brain.authority``) and building its projections. Astrolift provisions the
persistence a cell needs, writes the selection into ``client.config.json``
and reports whether the brain has adopted it.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

#: The Mimwell commit the ports below are held to.
MIMWELL_SOURCE = "mimwell-pvt 7e103f9"
#: ``app_slug`` (``deploy.app_slug``) is held to the commit that added it.
MIMWELL_APP_SLUG_SOURCE = "mimwell-pvt 416dbf5"
#: An Astrolift app slug, and the rule Mimwell holds ``deploy.app_slug`` to: a
#: letter, then lowercase letters, digits and dashes, 1 to 63 characters, not
#: ending in a dash.
APP_SLUG = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
#: Mimwell's default app name is ``<client.slug>-brain``.
BRAIN_SUFFIX = "-brain"

AXES: dict[str, tuple[str, ...]] = {
    "authority": ("files", "sqlite", "postgres", "mysql", "mongo", "d1"),
    "blobs": ("fs", "db-blob", "s3", "azure-blob"),
    "vectors": ("local", "in-db", "edge-db", "external"),
    "graph": ("edges-table", "age"),
}
DEFAULT_CELL = {"authority": "files", "blobs": "fs", "vectors": "local", "graph": "edges-table"}
DATABASE_DRIVERS = ("sqlite", "postgres", "mysql")
BLOB_DATABASE_DRIVERS = (*DATABASE_DRIVERS, "mongo")
#: Placements with no driver yet, and the Mimwell issue that makes them supported.
PLANNED: dict[tuple[str, str], int] = {}

#: ``deploy.persistence`` values (client.config.schema.json) and the ones an
#: Astrolift host renders; the rest belong to the Worker path.
PERSISTENCE = ("none", "d1", "s3", "efs", "postgres", "sqlite-volume", "kv")
ASTROLIFT_PERSISTENCE = ("sqlite-volume", "efs", "postgres", "s3")
#: Persistence that holds data: dropping it is a knowledge-store cutover, not a config change.
DATA_PERSISTENCE = ("sqlite-volume", "efs", "postgres")
MAX_REPLICAS = 20
DEFAULT_PORT = 8080
#: The template-owned portal container (mimwell-pvt#479), built from the repo root.
DOCKERFILE = "runtime/portal-container/Dockerfile"
#: A brain's memory: a small one uses about 40 MiB, one of 20,000 records
#: about 250 MiB (#2441). A brain's manifest that asks more keeps it.
MEMORY_REQUEST = "128Mi"
MEMORY_LIMIT = "512Mi"


class SubstrateError(Exception):
    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def cell_name(cell: dict[str, str]) -> str:
    return ", ".join(f"{axis}={cell[axis]}" for axis in AXES)


def cell_status(cell: dict[str, str]) -> tuple[str, str]:
    """(status, reason) of one matrix cell: supported, planned or unsupported.
    A port of Mimwell's ``knowledge_store.cell_status``."""
    authority = cell["authority"]
    if authority == "d1":
        return "unsupported", "d1 is a projection target, never an authority"
    if cell["blobs"] == "db-blob" and authority not in BLOB_DATABASE_DRIVERS:
        return "unsupported", f"db-blob keeps blobs in the authority database and {authority} has none"
    if cell["vectors"] == "in-db" and authority == "mongo":
        return (
            "unsupported",
            "in-db vectors are SQL vector columns (sqlite-vec, pgvector, MySQL) and mongo has none",
        )
    if cell["vectors"] == "in-db" and authority not in DATABASE_DRIVERS:
        return "unsupported", f"in-db vectors live in the authority database and {authority} has none"
    if cell["graph"] == "age" and authority != "postgres":
        return "unsupported", "age is a Postgres extension"
    issues = sorted({PLANNED[(axis, cell[axis])] for axis in AXES if (axis, cell[axis]) in PLANNED})
    if issues:
        return "planned", " ".join(f"#{issue}" for issue in issues)
    return "supported", ""


@dataclasses.dataclass(frozen=True)
class Selection:
    """A cell plus what the host attaches, as ``createBrain`` and
    ``updateBrainSubstrate`` take it."""

    authority: str = "files"
    blobs: str = "fs"
    vectors: str = "local"
    graph: str = "edges-table"
    persistence: tuple[str, ...] = ()
    replicas: int = 1
    image: str = ""
    port: int = DEFAULT_PORT

    @property
    def cell(self) -> dict[str, str]:
        return {
            "authority": self.authority,
            "blobs": self.blobs,
            "vectors": self.vectors,
            "graph": self.graph,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.cell,
            "persistence": list(self.persistence),
            "replicas": self.replicas,
            "image": self.image,
            "port": self.port,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Selection:
        data = dict(data or {})
        return cls(
            authority=data.get("authority") or "files",
            blobs=data.get("blobs") or "fs",
            vectors=data.get("vectors") or "local",
            graph=data.get("graph") or "edges-table",
            persistence=tuple(data.get("persistence") or ()),
            replicas=int(data.get("replicas") or 1),
            image=data.get("image") or "",
            port=int(data.get("port") or DEFAULT_PORT),
        )


def validate(selection: Selection) -> Selection:
    """The selection with its persistence normalized, or ``SubstrateError``
    naming the cell or the value that cannot be hosted."""
    for axis, value in selection.cell.items():
        if value not in AXES[axis]:
            raise SubstrateError(
                f"{axis}={value} is not a {axis} placement; one of {', '.join(AXES[axis])}", field=axis
            )
    status, reason = cell_status(selection.cell)
    if status != "supported":
        detail = f"no driver until {reason}" if status == "planned" else reason
        raise SubstrateError(f"{status} cell: {cell_name(selection.cell)}: {detail}", field="authority")
    persistence: list[str] = []
    for value in selection.persistence:
        if value not in PERSISTENCE:
            raise SubstrateError(
                f"persistence {value!r} is not one of {', '.join(PERSISTENCE)}", field="persistence"
            )
        if value in ("d1", "kv"):
            raise SubstrateError(
                f"persistence {value!r} belongs to the Worker path, not Astrolift", field="persistence"
            )
        if value != "none" and value not in persistence:
            persistence.append(value)
    held = set(persistence)
    needs = {
        ("authority", "sqlite"): "sqlite-volume",
        ("authority", "postgres"): "postgres",
        ("graph", "age"): "postgres",
        ("blobs", "s3"): "s3",
    }
    for (axis, value), required in needs.items():
        if selection.cell[axis] == value and required not in held:
            raise SubstrateError(
                f"{axis}={value} needs persistence {required!r} on Astrolift", field="persistence"
            )
    if not 1 <= selection.replicas <= MAX_REPLICAS:
        raise SubstrateError(f"replicas must be between 1 and {MAX_REPLICAS}", field="replicas")
    if selection.image and any(ch.isspace() for ch in selection.image):
        raise SubstrateError("image must be an image reference", field="image")
    if not 1 <= selection.port <= 65535:
        raise SubstrateError("port must be between 1 and 65535", field="port")
    return dataclasses.replace(selection, persistence=tuple(persistence))


def dropped_data(before: Selection, after: Selection) -> list[str]:
    """Persistence that holds data and ``after`` no longer attaches."""
    return [p for p in before.persistence if p in DATA_PERSISTENCE and p not in after.persistence]


# ---------------------------------------------------------------------------
# client.config.json and astrolift.toml
# ---------------------------------------------------------------------------


def apply_to_config(
    config: dict[str, Any], selection: Selection, *, slug: str, name: str | None = None
) -> dict[str, Any]:
    """``client.config.json`` with the selection written where Mimwell reads it:
    ``deploy.*`` always, and the legacy ``brain.blobs`` / ``semantic.substrate``
    fields where the selection has a value for them. ``brain.authority`` and
    ``contract.json`` are the brain's own cutover's to write.

    A config that Mimwell would not name ``slug`` is made to: a slug ending in
    ``-brain`` sets ``client.slug`` to the rest and drops ``deploy.app_slug``;
    any other is both ``client.slug`` and ``deploy.app_slug``. A config that
    already names it keeps its ``client.slug``, the brain's identity."""
    data = dict(config)
    deploy = dict(data.get("deploy") or {})
    if not _names(data, slug):
        client = dict(data.get("client") or {})
        if slug.endswith(BRAIN_SUFFIX) and slug != BRAIN_SUFFIX:
            client["slug"] = slug[: -len(BRAIN_SUFFIX)]
            deploy.pop("app_slug", None)
        elif APP_SLUG.fullmatch(slug):
            client["slug"] = slug
            deploy["app_slug"] = slug
        else:
            raise SubstrateError(
                f"{slug!r} cannot be written as deploy.app_slug: a brain's slug starts with a letter "
                "and is 1 to 63 lowercase letters, digits and dashes, not ending in a dash",
                field="slug",
            )
        data["client"] = client
    brain = dict(data.get("brain") or {})
    if name:
        brain["name"] = name
    if selection.blobs == "s3":
        brain["blobs"] = "s3"
    elif selection.blobs == "fs" and "efs" in selection.persistence:
        brain["blobs"] = "efs"
    data["brain"] = brain
    if selection.vectors in ("local", "edge-db", "external"):
        semantic = dict(data.get("semantic") or {})
        semantic["substrate"] = selection.vectors
        data["semantic"] = semantic
    deploy.update(target="astrolift", persistence=list(selection.persistence), replicas=selection.replicas)
    if selection.image:
        deploy.update(image=selection.image, port=selection.port)
    else:
        deploy.pop("image", None)
        deploy["port"] = selection.port
    data["deploy"] = deploy
    return data


def app_slug(config: dict[str, Any]) -> str:
    """The app slug ``gen-astrolift.py`` renders for ``config``:
    ``deploy.app_slug`` verbatim when set, else ``<client.slug>-brain``. A
    port of its ``_app_slug``, refusal of a malformed value included."""
    value = (config.get("deploy") or {}).get("app_slug")
    if value is None:
        return f"{(config.get('client') or {}).get('slug') or 'brain'}{BRAIN_SUFFIX}"
    if not isinstance(value, str) or not APP_SLUG.fullmatch(value):
        raise SubstrateError(f"deploy.app_slug {value!r} is not an Astrolift slug", field="slug")
    return value


def _names(config: dict[str, Any], slug: str) -> bool:
    try:
        return app_slug(config) == slug
    except SubstrateError:
        return False


def adds_app_slug(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Whether ``after`` sets a ``deploy.app_slug`` that ``before`` does not have."""
    value = (after.get("deploy") or {}).get("app_slug")
    return value is not None and value != (before.get("deploy") or {}).get("app_slug")


def declares_app_slug(schema: Any) -> bool:
    """Whether a Mimwell ``client.config.schema.json`` declares
    ``deploy.app_slug``. A version from before it refuses the key (``deploy``
    takes no other) and names the app ``<client.slug>-brain`` regardless."""
    try:
        return "app_slug" in schema["properties"]["deploy"]["properties"]
    except (KeyError, TypeError):
        return False


def render_manifest(
    selection: Selection, *, slug: str, display_name: str, environment_name: str = "production"
) -> str:
    """``astrolift.toml`` by ``gen-astrolift.py``'s mapping, plus ``[app] kind =
    "mimwell"``. With no image the container builds from the edition's
    portal Dockerfile, which the script cannot express yet."""
    import tomli_w

    persistence = set(selection.persistence)
    container: dict[str, Any] = {"name": "brain", "is_primary": True}
    if selection.image:
        container["image_ref"] = selection.image
    else:
        container.update(dockerfile_path=DOCKERFILE, build_context=".")
    container.update(
        port=selection.port,
        healthcheck={"kind": "http", "value": "/health", "port": selection.port},
    )
    workload: dict[str, Any] = {
        "name": "brain",
        "kind": "statefulset" if "sqlite-volume" in persistence else "deployment",
        "replicas": selection.replicas,
        "is_public": True,
        "memory_request": MEMORY_REQUEST,
        "memory_limit": MEMORY_LIMIT,
        "containers": [container],
    }
    volumes = []
    if "sqlite-volume" in persistence:
        volumes.append(
            {
                "name": "brain-data",
                "kind": "pvc",
                "mount_path": "/data",
                "size": "5Gi",
                "access_mode": "ReadWriteOnce",
            }
        )
    if "efs" in persistence:
        volumes.append(
            {
                "name": "brain-shared",
                "kind": "pvc",
                "mount_path": "/shared",
                "size": "20Gi",
                "access_mode": "ReadWriteMany",
            }
        )
    if volumes:
        workload["volumes"] = volumes
    document: dict[str, Any] = {
        "astrolift_version": 1,
        "name": slug,
        "app": {"slug": slug, "display_name": display_name, "kind": "mimwell"},
        "environments": {environment_name: {}},
        "workloads": [workload],
    }
    if "postgres" in persistence:
        config: dict[str, Any] = {"version": "17", "size": "small"}
        if selection.graph == "age":
            config["age"] = True
        document["managed_services"] = [{"kind": "postgres", "name": "brain", "config": config}]
    text = tomli_w.dumps(document)
    return text + S3_COMMENT if "s3" in persistence else text


def resources(app: Any, selection: Selection) -> list[dict[str, Any]]:
    """What the selection asks the platform for, with each one's state:
    the database from its managed service, volumes as declared (the cluster
    owns their claims), and s3 as unbound (Astrolift has no bucket binding)."""
    from astrolift_services.models import ManagedService

    out: list[dict[str, Any]] = []
    persistence = set(selection.persistence)
    if "postgres" in persistence:
        rows = list(
            ManagedService.objects.filter(registered_app=app, kind="postgres", deleted_at__isnull=True)
            .select_related("app_environment")
            .order_by("app_environment__name", "name")
        )
        if not rows:
            out.append(
                {
                    "kind": "postgres",
                    "name": "brain",
                    "environment": "",
                    "state": "pending",
                    "detail": "not created yet",
                }
            )
        for row in rows:
            out.append(
                {
                    "kind": "postgres",
                    "name": row.name,
                    "environment": row.app_environment.name if row.app_environment_id else "",
                    "state": row.status,
                    "detail": (row.status_error or "")[:500]
                    or ("DATABASE_URL" if row.status == "active" else ""),
                }
            )
    for persistence_key, volume, mount in (
        ("sqlite-volume", "brain-data", "/data"),
        ("efs", "brain-shared", "/shared"),
    ):
        if persistence_key in persistence:
            out.append(
                {
                    "kind": persistence_key,
                    "name": volume,
                    "environment": "",
                    "state": "declared",
                    "detail": mount,
                }
            )
    if "s3" in persistence:
        out.append(
            {
                "kind": "s3",
                "name": "blobs",
                "environment": "",
                "state": "unbound",
                "detail": "Astrolift has no bucket binding yet; bind the bucket's secret bundle outside the manifest",
            }
        )
    return out


S3_COMMENT = (
    "\n# s3: object storage for blobs (substrate.blobs = s3). Buckets are bound outside\n"
    "# the manifest (managed-service binding / secret bundle), never as env credentials.\n"
)


def _keep_own(rendered: dict[str, Any], current: dict[str, Any]) -> None:
    """What the brain's own ``brain`` workload keeps over the rendered one:
    more memory than the defaults, and the Dockerfile it builds from (a brain
    created before the template owned the container builds from its own)."""
    from astrolift_lifecycle.preview_cost import parse_memory_bytes

    for key in ("memory_request", "memory_limit"):
        try:
            if parse_memory_bytes(str(current.get(key) or "")) > parse_memory_bytes(rendered[key]):
                rendered[key] = current[key]
        except ValueError:
            continue
    own = next(iter(current.get("containers") or []), None) or {}
    container = rendered["containers"][0]
    if "dockerfile_path" in container and own.get("dockerfile_path"):
        container["dockerfile_path"] = own["dockerfile_path"]
        container["build_context"] = own.get("build_context") or "."


def merge_manifest(current: str, rendered: str) -> str:
    """``rendered`` laid over an existing ``astrolift.toml``: the ``brain``
    workload, the ``brain`` postgres service and ``[app]``'s slug, name and
    kind are replaced; everything else the brain's manifest declares
    (ingress, other workloads, other services, its environments) stays."""
    import tomllib

    import tomli_w

    try:
        base = tomllib.loads(current) if current.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise SubstrateError(f"the brain's astrolift.toml does not parse: {exc}") from exc
    new = tomllib.loads(rendered)
    out = dict(base)
    out["astrolift_version"] = new["astrolift_version"]
    out["name"] = new["name"]
    out["app"] = {**(base.get("app") or {}), **new["app"]}
    out["environments"] = base.get("environments") or new["environments"]
    others = [w for w in base.get("workloads") or [] if w.get("name") != "brain"]
    current = next((w for w in base.get("workloads") or [] if w.get("name") == "brain"), None)
    if current is not None:
        _keep_own(new["workloads"][0], current)
    out["workloads"] = [*new["workloads"], *others]
    services = [
        s
        for s in base.get("managed_services") or []
        if not (s.get("kind") == "postgres" and s.get("name") == "brain")
    ]
    services = [*new.get("managed_services", []), *services]
    if services:
        out["managed_services"] = services
    else:
        out.pop("managed_services", None)
    text = tomli_w.dumps(out)
    return text + S3_COMMENT if S3_COMMENT in rendered else text
