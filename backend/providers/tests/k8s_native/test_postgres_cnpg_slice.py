"""CNPG per-preview slices (#1578 feature 3, first driver).

The test that matters most is `test_the_parent_cluster_is_applied_whole`.
The shorter implementation patches the parent `Cluster` with only
`spec.managed.roles`, and under this repo's `server_side_apply` -- one
field manager, `force_conflicts=True` -- that makes the platform relinquish
every field it owns and is not carrying, pruning `spec.instances` and
`spec.storage` off a live production database. I wrote that version first.

Second most important: the slice must not hand the preview the parent's
credentials. Overriding only `POSTGRES_DB` passes every guard in
`SliceResult` and still lets a workload with a hardcoded connection string
reach production data, which is the failure #1578 was filed about.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import ServiceHandle, SliceSpec
from k8s_native.managed._handle import pack as pack_handle
from k8s_native.managed.postgres_cnpg import CNPGConfig, CNPGPostgresDriver

PARENT_SPEC = {
    "instances": 3,
    "storage": {"size": "100Gi", "storageClass": "gp3"},
    "resources": {"requests": {"cpu": "1", "memory": "4Gi"}},
    "bootstrap": {"initdb": {"database": "app", "owner": "app"}},
}


@dataclass
class FakeApplyResult:
    errors: list = field(default_factory=list)

    def summary(self):
        return [str(e) for e in self.errors]


@dataclass
class FakeClusterDriver:
    parent: dict | None = None
    applied: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    objects: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def get_manifest(self, cluster, namespace, kind, name):
        self.calls.append(("get", cluster, namespace, kind, name))
        if kind.endswith("/Cluster"):
            return deepcopy(self.parent)
        return deepcopy(self.objects.get((kind.rsplit("/", 1)[-1], name)))

    def apply_manifests(self, cluster, namespace, manifests, **kwargs):
        self.calls.append(("apply", cluster, namespace, kwargs.get("create_only", False)))
        self.applied.extend(deepcopy(manifests))
        for manifest in manifests:
            obj = deepcopy(manifest)
            obj["metadata"].setdefault("uid", "test-owned-uid")
            obj["metadata"].setdefault("resourceVersion", "2")
            if obj["kind"] == "Cluster":
                self.parent = obj
            else:
                self.objects[(obj["kind"], obj["metadata"]["name"])] = obj
        return FakeApplyResult()

    def delete_manifests(self, cluster, namespace, manifests):
        self.deleted.extend(deepcopy(manifests))
        for manifest in manifests:
            self.objects.pop((manifest["kind"], manifest["metadata"]["name"]), None)
        return FakeApplyResult()


class FakeStore:
    def __init__(self):
        self.values, self.writes, self.reads = {}, [], []

    def get(self, path):
        self.reads.append(path)
        return deepcopy(self.values.get(path))

    def upsert(self, path, value):
        self.writes.append(path)
        self.values[path] = deepcopy(value)


def _parent_object(roles: list[dict[str, Any]] | None = None) -> dict:
    spec = dict(PARENT_SPEC)
    if roles is not None:
        spec["managed"] = {"roles": roles}
    return {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Cluster",
        "metadata": {
            "name": "pg-main",
            "namespace": "acme-app",
            "uid": "parent-uid",
            "resourceVersion": "1",
            "labels": {
                "astrolift.io/managed-by": "platform",
                "astrolift.io/organization": "acme",
                "astrolift.io/app": "app",
            },
        },
        "spec": spec,
    }


def _handle() -> ServiceHandle:
    return ServiceHandle(
        handle=pack_handle(
            kind="postgres",
            cluster_id="cl-1",
            namespace="acme-app",
            name="pg-main",
        ),
        managed_service_id="service-id",
    )


def _driver(parent: dict | None = None):
    fake = FakeClusterDriver(parent=parent)
    return CNPGPostgresDriver(config=CNPGConfig(cluster_driver=fake, secrets_backend=FakeStore())), fake


def _spec(slice_id="preview-pr-42") -> SliceSpec:
    return SliceSpec(
        slice_id=slice_id,
        parent=_handle(),
        organization_id="org-id",
        app_id="app-id",
        environment_id=slice_id,
        labels={"astrolift.io/organization": "acme", "astrolift.io/app": "app"},
    )


def _of_kind(manifests, kind):
    return [m for m in manifests if m.get("kind") == kind]


# ---------------------------------------------------------------------------
# The parent-spec safety property
# ---------------------------------------------------------------------------


def test_the_parent_cluster_is_applied_whole():
    """The one that has to hold.

    `server_side_apply` uses a single field manager with
    `force_conflicts=True`, so a partial Cluster apply relinquishes every
    field that manager owns and is not carrying. `spec.instances` and
    `spec.storage` would be pruned off a live production database by the
    first `provision_slice` call.
    """
    driver, fake = _driver(parent=_parent_object())

    driver.provision_slice(_spec())

    (cluster_manifest,) = _of_kind(fake.applied, "Cluster")
    for key, value in PARENT_SPEC.items():
        assert cluster_manifest["spec"][key] == value, f"{key} was dropped from the parent spec"


def test_the_role_is_added_without_disturbing_an_existing_one():
    """Two previews on one cluster. Replacing the list would revoke the
    other preview's login."""
    existing = {"name": "slice_preview_pr_1_owner", "ensure": "present", "login": True}
    driver, fake = _driver(parent=_parent_object(roles=[existing]))

    driver.provision_slice(_spec("preview-pr-2"))

    (cluster_manifest,) = _of_kind(fake.applied, "Cluster")
    names = {r["name"] for r in cluster_manifest["spec"]["managed"]["roles"]}
    assert existing["name"] in names
    assert len(names) == 2


def test_reprovisioning_the_same_slice_does_not_duplicate_its_role():
    """Idempotent: the caller has to be able to ask for the same slice
    twice."""
    driver, fake = _driver(parent=_parent_object())
    driver.provision_slice(_spec())
    first = _of_kind(fake.applied, "Cluster")[0]["spec"]["managed"]["roles"]

    fake.parent = {**_parent_object(), "spec": {**PARENT_SPEC, "managed": {"roles": first}}}
    fake.applied.clear()
    driver.provision_slice(_spec())

    roles = _of_kind(fake.applied, "Cluster")[0]["spec"]["managed"]["roles"]
    assert len(roles) == 1


def test_an_unreadable_parent_refuses_before_credentials_or_apply():
    driver, fake = _driver(parent=None)
    with pytest.raises(ValueError, match="parent is unavailable"):
        driver.provision_slice(_spec())
    assert fake.applied == []
    assert driver._config.secrets_backend.reads == []
    assert driver._config.secrets_backend.writes == []


def test_the_role_is_applied_before_the_database():
    """CNPG's Database reconciler needs its owner to exist, and the role
    reconciler reads the password Secret."""
    driver, fake = _driver(parent=_parent_object())

    driver.provision_slice(_spec())

    kinds = [m["kind"] for m in fake.applied]
    assert kinds.index("Secret") < kinds.index("Cluster") < kinds.index("Database")


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_the_slice_does_not_reuse_the_parents_credentials():
    """The shorter implementation overrides only POSTGRES_DB and passes
    every guard in SliceResult, while leaving the preview holding a
    credential that opens the parent database."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())

    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_USER", "DATABASE_PASSWORD"):
        assert key in result.env_overrides, f"{key} must not fall through to the parent"


def test_both_the_canonical_and_alias_database_keys_are_overridden():
    """A half-applied override is worse than none: it looks isolated while
    a workload reading DATABASE_NAME still points at the parent."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())

    assert result.env_overrides["POSTGRES_DB"].literal == result.env_overrides["DATABASE_NAME"].literal


def test_database_url_is_replaced_wholesale():
    """It is one composed string in the parent's Secret and cannot be
    partially overridden, so leaving it would hand the workload the parent's
    full DSN."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())

    assert result.env_overrides["DATABASE_URL"].secret_ref
    assert "slice" in result.env_overrides["DATABASE_URL"].secret_ref


def test_host_and_port_name_the_same_authoritative_parent_namespace():
    """It is the same instance. Overriding them would be wrong and would
    also hide a misconfiguration."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())

    assert result.env_overrides["POSTGRES_HOST"].literal == "pg-main-rw.acme-app.svc"
    assert result.env_overrides["POSTGRES_PORT"].literal == "5432"


def test_runtime_credentials_are_independent_durable_and_not_binding_literals():
    driver, fake = _driver(parent=_parent_object())
    result = driver.provision_slice(_spec())
    (secret,) = _of_kind(fake.applied, "Secret")
    assert set(secret["stringData"]) == {"username", "password"}
    path = result.env_overrides["POSTGRES_PASSWORD"].secret_ref.split("#")[0]
    assert path == "services/org-id/app-id/cnpg-slices/service-id/preview-pr-42"
    assert secret["stringData"]["password"] == driver._config.secrets_backend.values[path]["password"]
    assert result.env_overrides["POSTGRES_PASSWORD"].literal is None


# ---------------------------------------------------------------------------
# Naming and teardown
# ---------------------------------------------------------------------------


def test_names_are_derived_from_the_slice_id_not_generated():
    """The caller has to be able to tear down the slice it means."""
    driver, _ = _driver(parent=_parent_object())

    first = driver.provision_slice(_spec()).slice_handle
    second = driver.provision_slice(_spec()).slice_handle

    assert first == second


def test_the_database_name_is_a_legal_postgres_identifier():
    """`preview-pr-42` is not: postgres identifiers may not contain `-`."""
    driver, _ = _driver(parent=_parent_object())

    result = driver.provision_slice(_spec())
    database = result.env_overrides["POSTGRES_DB"].literal

    assert "-" not in database
    assert database.replace("_", "").isalnum()


def test_deprovision_removes_the_database_and_secret():
    driver, fake = _driver(parent=_parent_object())
    handle = driver.provision_slice(_spec()).slice_handle

    assert driver.deprovision_slice(_spec(), handle) is True

    kinds = {m["kind"] for m in fake.deleted}
    assert kinds == {"Database", "Secret"}


def test_deprovision_leaves_the_parents_role_list_alone():
    """Removing the role is a read-modify-write on a shared spec from a
    teardown path, which can drop a role another slice added concurrently.
    An orphaned login with no database is a much smaller problem than a live
    preview losing its credentials."""
    driver, fake = _driver(parent=_parent_object())
    handle = driver.provision_slice(_spec()).slice_handle
    fake.applied.clear()

    driver.deprovision_slice(_spec(), handle)

    assert _of_kind(fake.deleted, "Cluster") == []
    assert _of_kind(fake.applied, "Cluster") == []


def test_a_failed_delete_returns_false_rather_than_raising():
    """A teardown workflow has to be able to record the leak and continue:
    a slice left behind is a database nobody sees until someone reads the
    instance's database list."""
    driver, fake = _driver(parent=_parent_object())
    handle = driver.provision_slice(_spec()).slice_handle

    def failing(cluster, namespace, manifests):
        return FakeApplyResult(errors=["boom"])

    fake.delete_manifests = failing

    assert driver.deprovision_slice(_spec(), handle) is False


def test_a_legacy_parent_handle_is_refused():
    """It carries no cluster or namespace locator, so the slice would land
    somewhere this driver cannot find it again."""
    driver, _ = _driver(parent=_parent_object())

    with pytest.raises(ValueError, match="recorded cluster and namespace"):
        driver.provision_slice(SliceSpec(slice_id="x", parent=ServiceHandle(handle="postgres/pg-main")))


def test_the_driver_declares_slicing_support():
    """`supports_slicing` requires both verbs; a driver that can create a
    slice and not remove one leaks a database on every teardown."""
    driver, _ = _driver()

    assert driver.supports_slicing() is True
    assert callable(driver.provision_slice)
    assert callable(driver.deprovision_slice)


@pytest.mark.parametrize("field", ["name", "namespace", "uid", "resourceVersion", "owner", "platform"])
def test_parent_authority_refusal_precedes_all_store_reads_and_mutations(field):
    parent = _parent_object()
    if field in ("name", "namespace"):
        parent["metadata"][field] = "foreign"
    elif field in ("uid", "resourceVersion"):
        parent["metadata"].pop(field)
    elif field == "owner":
        parent["metadata"]["labels"]["astrolift.io/organization"] = "foreign"
    else:
        parent["metadata"]["labels"].pop("astrolift.io/managed-by")
    driver, fake = _driver(parent=parent)
    with pytest.raises(ValueError):
        driver.provision_slice(_spec())
    assert not driver._config.secrets_backend.reads
    assert not driver._config.secrets_backend.writes
    assert not fake.applied


@pytest.mark.parametrize("kind", ["Secret", "Database"])
def test_foreign_existing_child_refuses_before_store_or_apply(kind):
    driver, fake = _driver(parent=_parent_object())
    driver.provision_slice(_spec())
    key = next(key for key in fake.objects if key[0] == kind)
    fake.objects[key]["metadata"]["labels"]["ai.astrolift/app-id"] = "foreign"
    fake.applied.clear()
    store = driver._config.secrets_backend
    store.reads.clear()
    writes = len(store.writes)
    with pytest.raises(ValueError):
        driver.provision_slice(_spec())
    assert not store.reads
    assert len(store.writes) == writes
    assert not fake.applied


def test_partial_database_failure_reuses_exact_credentials_and_secret(monkeypatch):
    driver, fake = _driver(parent=_parent_object())
    issued = []
    monkeypatch.setattr(
        "k8s_native.managed._cnpg_slices.secrets.token_urlsafe",
        lambda size: issued.append(size) or "test-only-p@ss:word",
    )
    apply = fake.apply_manifests
    failed = []

    def fail_once(cluster, namespace, manifests, **kwargs):
        if manifests[0]["kind"] == "Database" and not failed:
            failed.append(True)
            return FakeApplyResult(errors=["controlled failure"])
        return apply(cluster, namespace, manifests, **kwargs)

    fake.apply_manifests = fail_once
    with pytest.raises(RuntimeError, match="Database creation failed"):
        driver.provision_slice(_spec())
    first_secret = deepcopy(_of_kind(fake.applied, "Secret")[0])
    result = driver.provision_slice(_spec())
    result_again = driver.provision_slice(_spec())
    assert result.slice_handle == result_again.slice_handle
    assert issued == [40]
    assert len(driver._config.secrets_backend.writes) == 1
    assert _of_kind(fake.applied, "Secret") == [first_secret]
    uri = next(iter(driver._config.secrets_backend.values.values()))["uri"]
    assert "test-only-p%40ss%3Aword@pg-main-rw.acme-app.svc:5432/" in uri
    assert all(m["metadata"]["resourceVersion"] == "1" for m in _of_kind(fake.applied, "Cluster"))


def test_two_services_in_one_namespace_use_distinct_slice_names():
    driver, _ = _driver(parent=_parent_object())
    first = _spec()
    from dataclasses import replace

    second = replace(first, parent=replace(first.parent, managed_service_id="another-service-id"))
    assert driver._slice_names(first) != driver._slice_names(second)
    assert all(len(value) <= 63 for value in driver._slice_names(second))


def test_render_only_slice_provision_and_removal_are_refused():
    driver = CNPGPostgresDriver()
    with pytest.raises(ValueError, match="live cluster"):
        driver.provision_slice(_spec())
    with pytest.raises(ValueError, match="live cluster"):
        driver.deprovision_slice(_spec(), "postgres_slice/cl-1/acme-app/anything")


def test_foreign_delete_handle_is_refused_without_cluster_calls():
    driver, fake = _driver(parent=_parent_object())
    with pytest.raises(ValueError, match="does not match"):
        driver.deprovision_slice(_spec(), "postgres_slice/foreign/elsewhere/other")
    assert not fake.calls
    assert not fake.deleted


def test_database_finalizer_pending_keeps_credentials_and_reports_incomplete():
    driver, fake = _driver(parent=_parent_object())
    handle = driver.provision_slice(_spec()).slice_handle

    def pending(cluster, namespace, manifests):
        fake.deleted.extend(manifests)
        return FakeApplyResult()

    fake.delete_manifests = pending
    assert driver.deprovision_slice(_spec(), handle) is False
    assert [obj["kind"] for obj in fake.deleted] == ["Database"]
    assert any(kind == "Secret" for kind, _ in fake.objects)


@pytest.mark.parametrize("kind", ["Secret", "Database"])
def test_creation_conflict_never_adopts_a_foreign_object_on_retry(kind):
    driver, fake = _driver(parent=_parent_object())
    apply = fake.apply_manifests
    conflict = []

    def collide(cluster, namespace, manifests, **kwargs):
        if manifests[0]["kind"] == kind and not conflict:
            assert kwargs.get("create_only") is True
            foreign = deepcopy(manifests[0])
            foreign["metadata"]["labels"]["ai.astrolift/app-id"] = "foreign"
            key = (kind, foreign["metadata"]["name"])
            fake.objects[key] = foreign
            conflict.append(key)
            return FakeApplyResult(errors=["controlled create conflict"])
        return apply(cluster, namespace, manifests, **kwargs)

    fake.apply_manifests = collide
    with pytest.raises(RuntimeError):
        driver.provision_slice(_spec())
    preserved = deepcopy(fake.objects[conflict[0]])
    fake.applied.clear()
    store = driver._config.secrets_backend
    store.reads.clear()
    writes = len(store.writes)
    with pytest.raises(ValueError):
        driver.provision_slice(_spec())
    assert not store.reads and len(store.writes) == writes
    assert not fake.applied
    assert fake.objects[conflict[0]] == preserved


def test_actual_secret_api_data_recovers_issued_credentials_without_rotation(monkeypatch):
    import base64

    driver, fake = _driver(parent=_parent_object())
    first = driver.provision_slice(_spec())
    key = next(key for key in fake.objects if key[0] == "Secret")
    obj = fake.objects[key]
    values = obj.pop("stringData")
    obj["data"] = {name: base64.b64encode(value.encode()).decode() for name, value in values.items()}
    store = driver._config.secrets_backend
    issued = deepcopy(store.values)
    store.values.clear()

    def refuse_mint(size):
        raise AssertionError("a retained issued credential must not rotate")

    monkeypatch.setattr("k8s_native.managed._cnpg_slices.secrets.token_urlsafe", refuse_mint)
    second = driver.provision_slice(_spec())
    assert first.slice_handle == second.slice_handle
    assert store.values == issued
    assert len(_of_kind(fake.applied, "Secret")) == 1
