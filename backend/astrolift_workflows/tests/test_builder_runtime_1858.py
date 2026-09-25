"""The App Builder runtime: binary files, the data file, the promoted app (#1858).

The renderer is exercised on rows read back from Postgres, because the
guid it names things by is a ``uuid.UUID`` there (slicing it crashed every
provision before this change). The cluster is the one fake: a recording
driver stands in for ``core.cluster_management._driver_for_cluster``.

The seed init container's shell is executed for real against the parts
the renderer emits, so "the app can read its data file" is checked on the
reassembled bytes, not on the manifest shape alone. The end-to-end
acceptance test, through the real endpoints, lives with the builder API
tests and reuses the helpers here.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import subprocess

import pytest
from _sdk.cluster import ApplyResult

from astrolift_identity.models import Team
from astrolift_lifecycle.models import DevEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.dev_environment import (
    _DATA_PART_BYTES,
    _build_manifests,
    _deploy_promoted_app_sync,
    _render_runtime,
    _sync_dev_environment_files_sync,
)
from core.app_deploy import namespace_for_app

pytestmark = pytest.mark.django_db


class _RecordingDriver:
    """Cluster driver double: records every call, reports what it is told."""

    def __init__(self, *, storage_classes=(), csi_drivers=(), secrets=()):
        self.storage_classes = list(storage_classes)
        self.csi_drivers = list(csi_drivers)
        self.secrets = list(secrets)
        self.namespaces: list[tuple[str, dict, dict]] = []
        self.applied: list[tuple[str, list[dict]]] = []
        self.deleted: list[tuple[str, list[dict]]] = []

    def list_storage_classes(self, cluster):
        return self.storage_classes

    def list_csi_drivers(self, cluster):
        return self.csi_drivers

    def ensure_namespace(self, cluster, name, labels, annotations):
        self.namespaces.append((name, labels, annotations))

    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):
        self.applied.append((namespace, manifests))
        refs = [f"{m['kind']}/{m['metadata']['name']}" for m in manifests]
        return ApplyResult(created=refs, updated=[], unchanged=[], errors=[])

    def list_manifests(self, cluster, namespace, kind):
        if kind != "Secret":
            return []
        return [{"metadata": {"name": name}} for name in self.secrets]

    def delete_manifests(self, cluster, namespace, manifests, *, propagation_policy=None):
        self.deleted.append((namespace, manifests))


@pytest.fixture
def driver(monkeypatch):
    recording = _RecordingDriver()
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: recording)
    return recording


def _dev_env(org, cluster, actor, **fields) -> DevEnvironment:
    dev = DevEnvironment.objects.create(
        organization=org,
        creator=actor,
        tenant_cluster=cluster,
        runtime=DevEnvironment.Runtime.PYTHON,
        port=8080,
        start_command="python server.py",
        status=DevEnvironment.Status.RUNNING,
        **fields,
    )
    return DevEnvironment.all_objects.select_related("organization", "tenant_cluster").get(pk=dev.pk)


def _by_kind(resources, kind):
    return [r for r in resources if r["kind"] == kind]


def _seed(resources, data_file: str, tmp_path) -> None:
    """Do what the kubelet and the seed-data init container would.

    Project every part Secret into a seed directory the way the pod's
    projected volume lays them out, then run the init container's shell
    with the pod paths pointed at ``tmp_path``.
    """
    deployment = _by_kind(resources, "Deployment")[0]
    pod = deployment["spec"]["template"]["spec"]
    secrets = {s["metadata"]["name"]: s for s in _by_kind(resources, "Secret")}
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir(exist_ok=True)
    projected = next(v for v in pod["volumes"] if v["name"] == "data-seed")["projected"]
    for source in projected["sources"]:
        secret = secrets[source["secret"]["name"]]
        for item in source["secret"]["items"]:
            (seed_dir / item["path"]).write_bytes(base64.b64decode(secret["data"][item["key"]]))
    init = next(c for c in pod["initContainers"] if c["name"] == "seed-data")
    assert init["command"][:2] == ["sh", "-c"]
    script = init["command"][2].replace("/seed/", f"{seed_dir}/")
    subprocess.run(
        ["sh", "-c", script],
        env={**os.environ, "ASTROLIFT_DATA_FILE": data_file},
        check=True,
    )


def _sqlite_bytes(tmp_path, rows: int, row_bytes: int) -> bytes:
    path = tmp_path / "source.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, payload BLOB)")
    conn.executemany(
        "INSERT INTO sales (id, payload) VALUES (?, ?)",
        [(i, hashlib.sha256(str(i).encode()).digest() * (row_bytes // 32)) for i in range(rows)],
    )
    conn.commit()
    conn.close()
    return path.read_bytes()


# ---- names ------------------------------------------------------------


def test_dev_env_renders_from_a_db_row_and_names_use_the_whole_guid(org, cluster, actor):
    """Regression: ``dev.guid[:8]`` raised TypeError on the UUID a row
    carries, so no dev env ever provisioned. The names now use the whole
    guid, because a UUIDv7 prefix is a timestamp two dev envs created in
    the same minute (in any orgs) would share."""
    first = _dev_env(org, cluster, actor)
    second = _dev_env(org, cluster, actor)

    first_ns, first_url, _ = _build_manifests(first)
    second_ns, second_url, _ = _build_manifests(second)

    assert first_ns == f"builder-dev-{first.guid.hex}"
    assert first_url == f"https://dev-{first.guid.hex}.acme-test.dev.astrolift.io"
    assert first_ns != second_ns
    assert first_url != second_url
    assert len(first_ns) <= 63


# ---- files --------------------------------------------------------------


def test_binary_files_render_as_configmap_binary_data(org, cluster, actor):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\xff").decode()
    dev = _dev_env(
        org,
        cluster,
        actor,
        files={"server.py": "print('hi')", "logo.png": {"content": png, "encoding": "base64"}},
    )

    _, _, resources = _build_manifests(dev)

    config_map = _by_kind(resources, "ConfigMap")[0]
    assert config_map["data"] == {"f0001": "print('hi')"}
    assert config_map["binaryData"] == {"f0000": png}
    assert _by_kind(resources, "Secret") == []


def test_a_file_tree_with_directories_maps_generated_keys_back_to_paths(org, cluster, actor):
    """ConfigMap keys are flat, so ``static/logo.png`` needs ``items`` (#1873)."""
    import re

    png = base64.b64encode(b"\x89PNG").decode()
    dev = _dev_env(
        org,
        cluster,
        actor,
        files={
            "server.py": "print('hi')",
            "static/css/site.css": "body{}",
            "static/logo.png": {"content": png, "encoding": "base64"},
        },
    )

    _, _, resources = _build_manifests(dev)

    config_map = _by_kind(resources, "ConfigMap")[0]
    keys = [*config_map["data"], *config_map.get("binaryData", {})]
    assert all(re.fullmatch(r"[-._a-zA-Z0-9]+", key) for key in keys)
    [deployment] = _by_kind(resources, "Deployment")
    [volume] = [v for v in deployment["spec"]["template"]["spec"]["volumes"] if v["name"] == "app-files"]
    by_path = {item["path"]: item["key"] for item in volume["configMap"]["items"]}
    assert set(by_path) == {"server.py", "static/css/site.css", "static/logo.png"}
    assert config_map["data"][by_path["static/css/site.css"]] == "body{}"
    assert config_map["binaryData"][by_path["static/logo.png"]] == png


# ---- data file ------------------------------------------------------------


def test_dev_env_data_file_ships_as_secret_parts_seeded_into_an_empty_dir(org, cluster, actor):
    data = os.urandom(10 * 1024 * 1024)
    dev = _dev_env(org, cluster, actor, data_file_path="db/data.sqlite", data_file=data)

    namespace, _, resources = _build_manifests(dev)

    parts = _by_kind(resources, "Secret")
    assert len(parts) == -(-len(data) // _DATA_PART_BYTES) == 12
    assert [p["metadata"]["name"] for p in parts] == [f"builder-data-part-{i:04d}" for i in range(12)]
    assert all(p["metadata"]["namespace"] == namespace for p in parts)
    decoded = [base64.b64decode(p["data"]["part"]) for p in parts]
    assert all(len(chunk) <= _DATA_PART_BYTES for chunk in decoded)
    assert b"".join(decoded) == data

    pod = _by_kind(resources, "Deployment")[0]["spec"]["template"]["spec"]
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["data"] == {"name": "data", "emptyDir": {}}
    assert [s["secret"]["name"] for s in volumes["data-seed"]["projected"]["sources"]] == [
        p["metadata"]["name"] for p in parts
    ]
    assert [c["name"] for c in pod["initContainers"]] == ["seed-data", "install-deps"]
    app = pod["containers"][0]
    assert {"name": "data", "mountPath": "/data"} in app["volumeMounts"]
    env = {e["name"]: e["value"] for e in app["env"]}
    assert env["ASTROLIFT_DATA_DIR"] == "/data"
    assert env["ASTROLIFT_DATA_FILE"] == "/data/db/data.sqlite"
    assert _by_kind(resources, "PersistentVolumeClaim") == []
    assert "strategy" not in _by_kind(resources, "Deployment")[0]["spec"]


def test_without_a_data_file_there_is_no_data_volume(org, cluster, actor):
    dev = _dev_env(org, cluster, actor, files={"index.html": "<p>hi</p>"})

    _, _, resources = _build_manifests(dev)

    pod = _by_kind(resources, "Deployment")[0]["spec"]["template"]["spec"]
    assert {v["name"] for v in pod["volumes"]} == {"app-files", "deps-cache"}
    assert [c["name"] for c in pod["initContainers"]] == ["install-deps"]
    assert "ASTROLIFT_DATA_FILE" not in {e["name"] for e in pod["containers"][0]["env"]}


def test_promoted_runtime_claims_a_pvc_when_the_cluster_can_provision_one(org, cluster, actor):
    dev = _dev_env(org, cluster, actor, data_file_path="data.sqlite", data_file=b"x" * 1024)

    resources = _render_runtime(
        dev,
        namespace="acme-shop",
        name="builder-app",
        hostname="acme-shop.example.test",
        data_storage_class="gp3",
    )

    claim = _by_kind(resources, "PersistentVolumeClaim")[0]
    assert claim["metadata"] == {"name": "builder-app-data", "namespace": "acme-shop"}
    assert claim["spec"]["storageClassName"] == "gp3"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert claim["spec"]["resources"]["requests"]["storage"] == "1Gi"
    deployment = _by_kind(resources, "Deployment")[0]
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    volumes = {v["name"]: v for v in deployment["spec"]["template"]["spec"]["volumes"]}
    assert volumes["data"] == {"name": "data", "persistentVolumeClaim": {"claimName": "builder-app-data"}}


def test_seed_reassembles_the_file_once_and_keeps_what_the_app_wrote(org, cluster, actor, tmp_path):
    data = _sqlite_bytes(tmp_path, rows=40, row_bytes=64 * 1024)
    assert len(data) > 2 * _DATA_PART_BYTES
    dev = _dev_env(org, cluster, actor, data_file_path="db/data.sqlite", data_file=data)
    _, _, resources = _build_manifests(dev)
    target = tmp_path / "data" / "db" / "data.sqlite"

    _seed(resources, str(target), tmp_path)

    assert target.read_bytes() == data
    conn = sqlite3.connect(target)
    assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert conn.execute("SELECT count(*) FROM sales").fetchone() == (40,)
    conn.execute("INSERT INTO sales (id, payload) VALUES (40, x'00')")
    conn.commit()
    conn.close()

    _seed(resources, str(target), tmp_path)

    conn = sqlite3.connect(target)
    assert conn.execute("SELECT count(*) FROM sales").fetchone() == (41,)
    conn.close()


def test_an_empty_data_file_still_seeds(org, cluster, actor, tmp_path):
    dev = _dev_env(org, cluster, actor, data_file_path="data.sqlite", data_file=b"")
    _, _, resources = _build_manifests(dev)
    target = tmp_path / "data" / "data.sqlite"

    _seed(resources, str(target), tmp_path)

    assert target.exists()
    assert target.read_bytes() == b""


# ---- sync ---------------------------------------------------------------


def test_sync_reapplies_the_full_deployment_and_prunes_stale_parts(org, cluster, actor, driver):
    """The old sync applied a Deployment carrying only an annotation;
    server-side apply reads that as the whole intent of the field
    manager and drops the rest of the spec. It now re-applies the full
    render, and deletes the parts a smaller data file left behind."""
    dev = _dev_env(org, cluster, actor, data_file_path="data.sqlite", data_file=b"y" * (_DATA_PART_BYTES + 1))
    dev.namespace = "builder-dev-existing"
    dev.save(update_fields=["namespace"])
    driver.secrets = [f"builder-data-part-{i:04d}" for i in range(5)] + ["unrelated-secret"]

    _sync_dev_environment_files_sync(dev.pk)

    namespace, resources = driver.applied[0]
    assert namespace == "builder-dev-existing"
    deployment = _by_kind(resources, "Deployment")[0]
    assert deployment["spec"]["selector"] == {"matchLabels": {"app": "builder-dev"}}
    assert deployment["spec"]["template"]["spec"]["containers"][0]["name"] == "app"
    assert "builder.astrolift.io/last-sync" in deployment["spec"]["template"]["metadata"]["annotations"]
    assert [s["metadata"]["name"] for s in _by_kind(resources, "Secret")] == [
        "builder-data-part-0000",
        "builder-data-part-0001",
    ]
    [(deleted_ns, deleted)] = driver.deleted
    assert deleted_ns == "builder-dev-existing"
    assert [m["metadata"]["name"] for m in deleted] == [f"builder-data-part-{i:04d}" for i in range(2, 5)]
    dev.refresh_from_db()
    assert dev.status == DevEnvironment.Status.RUNNING


def test_sync_after_the_data_file_is_removed_prunes_every_part(org, cluster, actor, driver):
    dev = _dev_env(org, cluster, actor, files={"app.py": "x"})
    dev.namespace = "builder-dev-existing"
    dev.save(update_fields=["namespace"])
    driver.secrets = ["builder-data-part-0000", "builder-data-part-0001"]

    _sync_dev_environment_files_sync(dev.pk)

    [(_, deleted)] = driver.deleted
    assert [m["metadata"]["name"] for m in deleted] == ["builder-data-part-0000", "builder-data-part-0001"]


# ---- promoted app ---------------------------------------------------------


def _promoted(org, cluster, actor, **fields):
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="Shop",
        slug="shop",
        source_kind=RegisteredApp.SourceKind.DIRECT_UPLOAD,
    )
    dev = _dev_env(org, cluster, actor, **fields)
    dev.promoted_app = app
    dev.status = DevEnvironment.Status.PROMOTING
    dev.save(update_fields=["promoted_app", "status"])
    return app, dev


def test_deploy_promoted_app_serves_from_the_app_namespace(org, cluster, actor, driver):
    app, dev = _promoted(org, cluster, actor, data_file_path="data.sqlite", data_file=b"z" * 10)

    result = _deploy_promoted_app_sync(dev.pk, "gp3")

    namespace = namespace_for_app(app)
    assert namespace == "acme-test-shop"
    assert result == {"namespace": namespace, "app_url": f"https://{namespace}.acme-test.dev.astrolift.io"}
    [(ensured, labels, _)] = driver.namespaces
    assert ensured == namespace
    assert labels == {
        "astrolift.io/managed-by": "astrolift",
        "astrolift.io/organization": "acme-test",
        "astrolift.io/app": "shop",
    }
    applied_ns, resources = driver.applied[0]
    assert applied_ns == namespace
    assert _by_kind(resources, "PersistentVolumeClaim")[0]["spec"]["storageClassName"] == "gp3"
    ingress = _by_kind(resources, "Ingress")[0]
    assert ingress["spec"]["rules"][0]["host"] == f"{namespace}.acme-test.dev.astrolift.io"
    assert ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == "builder-app"


def test_deploy_promoted_app_without_storage_uses_an_empty_dir(org, cluster, actor, driver):
    _, dev = _promoted(org, cluster, actor, data_file_path="data.sqlite", data_file=b"z" * 10)

    _deploy_promoted_app_sync(dev.pk, "")

    _, resources = driver.applied[0]
    assert _by_kind(resources, "PersistentVolumeClaim") == []
    pod = _by_kind(resources, "Deployment")[0]["spec"]["template"]["spec"]
    assert {"name": "data", "emptyDir": {}} in pod["volumes"]
