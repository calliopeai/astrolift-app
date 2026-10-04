"""Private renderer metadata plus actual committed PG/TLS/GAPIC receipt seam."""

import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from astrolift_lifecycle.models import Deployment
from astrolift_services.gcp_gke_preparation_journal import PreparationJournalError, preparation_journal_mutex
from astrolift_services.gcp_workload_identity_journal import journal_mutex
from astrolift_services.models import GCPWorkloadIdentityJournal
from astrolift_services.tests.test_gcp_gke_preparation_journal_2278 import (
    admitted,
    admitted_iam,
    annotate,
    iam_operation,
    prepare,
    reconcile_iam,
)
from astrolift_services.tests.test_gcp_gke_preparation_journal_2278 import bridge as journal_bridge
from astrolift_services.tests.test_gcp_gke_preparation_journal_2278 import native_tls as journal_native_tls
from astrolift_services.tests.test_gcp_gke_preparation_journal_2278 import world as journal_world
from core.app_deploy import AppDeployError, render_resources_for_deployment
from core.gcp_prepared_identity_render import (
    PreparedGCPIdentity,
    PreparedIdentityRenderError,
    inject_prepared_identity,
    prepared_controller_fingerprints,
    prepared_identity_for_deployment,
    validate_prepared_resources,
)
from core.permissions import PermissionDenied


@pytest.fixture
def world(monkeypatch, client, request):
    return journal_world.__wrapped__(monkeypatch, client, request)


@pytest.fixture
def native_tls(tmp_path, monkeypatch):
    yield from journal_native_tls.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def bridge(world, native_tls):
    return journal_bridge.__wrapped__(world, native_tls)


def identity():
    return PreparedGCPIdentity(
        *(str(uuid4()) for _ in range(6)),
        "original-namespace",
        str(uuid4()),
        "original-ksa",
        str(uuid4()),
        "original-gsa@project-one.iam.gserviceaccount.com",
        "123",
        str(uuid4()),
        1,
        str(uuid4()),
        1,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        str(uuid4()),
        1,
    )


def controller(kind="Deployment"):
    spec = {
        "selector": {"matchLabels": {"app": "original"}},
        "template": {
            "metadata": {"labels": {"app": "original", "foreign": "preserved"}},
            "spec": {
                "containers": [{"name": "web", "image": "example.invalid/image@sha256:" + "d" * 64}],
                "nodeSelector": {"pool": "original"},
            },
        },
    }
    if kind != "DaemonSet":
        spec["replicas"] = 1
    return {"apiVersion": "apps/v1", "kind": kind, "metadata": {"name": "web"}, "spec": spec}


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet"])
def test_supported_controllers_retain_exact_identities_without_fabricated_native_target(kind):
    value = identity()
    resources = [
        controller(kind),
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": value.namespace, "uid": value.namespace_uid},
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": value.service_account_name, "uid": value.service_account_uid},
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "binding"},
            "stringData": {"private": "never-in-fingerprint-record"},
        },
    ]
    before = deepcopy(resources)
    rendered = inject_prepared_identity(resources, value)
    assert resources == before
    assert [r["kind"] for r in rendered] == [kind, "Secret"]
    pod = rendered[0]["spec"]["template"]
    assert pod["spec"]["serviceAccountName"] == value.service_account_name
    assert pod["metadata"]["labels"]["foreign"] == "preserved"
    assert value.owner_labels.items() <= pod["metadata"]["labels"].items()
    (receipt,) = prepared_controller_fingerprints(rendered, value)
    assert receipt.namespace == value.namespace and receipt.service_account_name == value.service_account_name
    assert receipt.replicas == (None if kind == "DaemonSet" else 1)
    assert "uid" not in asdict(receipt) and "generation" not in asdict(receipt)
    assert "never-in-fingerprint-record" not in json.dumps(asdict(receipt))


@pytest.mark.parametrize(
    "change,reason",
    [
        ("sa", "SERVICE_ACCOUNT_CONFLICT"),
        ("legacy_sa", "SERVICE_ACCOUNT_CONFLICT"),
        ("owner", "OWNER_CONFLICT"),
        ("gsa", "IDENTITY_CONFLICT"),
        ("selector", "SELECTOR_MISMATCH"),
        ("unknown_selector", "SELECTOR_UNSUPPORTED"),
        ("uid", "CONTROLLER_IDENTITY_UNESTABLISHED"),
        ("host", "SERVICE_ACCOUNT_CONFLICT"),
        ("zero", "REPLICAS_UNSUPPORTED"),
        ("bool", "REPLICAS_UNSUPPORTED"),
        ("missing", "REPLICAS_UNSUPPORTED"),
        ("api", "WORKLOAD_UNSUPPORTED"),
    ],
)
def test_conflicts_refuse_before_output(change, reason):
    resource = controller()
    pod = resource["spec"]["template"]["spec"]
    if change == "sa":
        pod["serviceAccountName"] = "foreign"
    elif change == "legacy_sa":
        pod["serviceAccount"] = "foreign"
    elif change == "owner":
        resource["metadata"]["labels"] = {"astrolift.io/app-id": str(uuid4())}
    elif change == "gsa":
        resource["spec"]["template"]["metadata"]["annotations"] = {
            "iam.gke.io/gcp-service-account": "foreign"
        }
    elif change == "selector":
        resource["spec"]["selector"]["matchLabels"]["app"] = "different"
    elif change == "unknown_selector":
        resource["spec"]["selector"] = {"unsupported": "value"}
    elif change == "uid":
        resource["metadata"]["uid"] = str(uuid4())
    elif change == "host":
        pod["hostNetwork"] = True
    elif change in ("zero", "bool"):
        resource["spec"]["replicas"] = 0 if change == "zero" else True
    elif change == "missing":
        del resource["spec"]["replicas"]
    elif change == "api":
        resource["apiVersion"] = "apps/v1beta1"
    with pytest.raises(PreparedIdentityRenderError, match=reason):
        inject_prepared_identity([resource], identity())


@pytest.mark.parametrize(
    "kind", ["HorizontalPodAutoscaler", "Job", "CronJob", "ReplicaSet", "Pod", "ClusterRole", "Unknown"]
)
def test_unsupported_shapes_do_not_silently_pass(kind):
    with pytest.raises(PreparedIdentityRenderError):
        inject_prepared_identity(
            [controller(), {"apiVersion": "v1", "kind": kind, "metadata": {"name": "unsupported"}}],
            identity(),
        )


@pytest.mark.parametrize("kind", ["Namespace", "ServiceAccount"])
def test_original_prepared_uid_cannot_be_replaced(kind):
    value = identity()
    name = value.namespace if kind == "Namespace" else value.service_account_name
    with pytest.raises(PreparedIdentityRenderError, match="CONFLICT"):
        inject_prepared_identity(
            [
                controller(),
                {"apiVersion": "v1", "kind": kind, "metadata": {"name": name, "uid": str(uuid4())}},
            ],
            value,
        )


def test_daemonset_cannot_invent_replicas():
    resource = controller("DaemonSet")
    resource["spec"]["replicas"] = 1
    with pytest.raises(PreparedIdentityRenderError, match="REPLICAS_UNSUPPORTED"):
        validate_prepared_resources([resource], identity())


def test_execution_and_placement_fingerprints_track_final_bytes():
    value = identity()
    resources = inject_prepared_identity([controller()], value)
    (original,) = prepared_controller_fingerprints(resources, value)
    resources[0]["spec"]["template"]["spec"]["containers"][0]["env"] = [
        {"name": "BOUND_REVISION", "value": "2"}
    ]
    (execution,) = prepared_controller_fingerprints(resources, value)
    assert execution.template_sha256 != original.template_sha256
    assert execution.request_sha256 != original.request_sha256
    assert execution.placement_sha256 == original.placement_sha256
    resources[0]["spec"]["template"]["spec"]["nodeSelector"]["pool"] = "different"
    (placement,) = prepared_controller_fingerprints(resources, value)
    assert placement.placement_sha256 != execution.placement_sha256
    assert not hasattr(placement, "spec")


_MANIFEST = """name = "original"
[env]
RENDER_PRIVATE_CANARY = "synthetic-private-value"
[[workloads]]
name = "web"
kind = "deployment"
replicas = 1
  [[workloads.containers]]
  name = "web"
  is_primary = true
  port = 8080
"""


def deployment(world):
    world.medops_app.manifest_raw = _MANIFEST
    world.medops_app.save(update_fields=["manifest_raw"])
    return Deployment.objects.create(
        registered_app=world.medops_app,
        app_environment=world.env,
        trigger_kind="manual",
        image_tag="reviewed",
        image_digest="sha256:" + "e" * 64,
    )


@contextmanager
def completed(world):
    world.http["before_write"] = None
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        prepare(world, store, reservation)
        native_sha = store.bind_native_union(reservation, checkpoint=admitted)
        with journal_mutex(store.iam_target(reservation, checkpoint=admitted)) as iam_store:
            iam_reservation = iam_store.reserve(
                iam_operation(world, native_sha), checkpoint=lambda c: admitted_iam(world, c)
            )
            reconcile_iam(world, iam_store, iam_reservation)
            iam = (iam_store, iam_reservation)
            annotate(world, store, reservation, iam)
            yield store, reservation, iam


def current_metadata(deploy, world, store, reservation, iam, **kwargs):
    return prepared_identity_for_deployment(
        deploy,
        store,
        reservation,
        iam=iam,
        checkpoint=kwargs.get("checkpoint", admitted),
        iam_checkpoint=kwargs.get("iam_checkpoint", lambda c: admitted_iam(world, c)),
    )


@pytest.mark.django_db(transaction=True)
def test_actual_completed_pg_tls_gapic_journals_factory_is_read_only_and_render_retains_original_names(
    bridge,
):
    w = bridge
    deploy = deployment(w)
    with completed(w) as (store, reservation, iam):
        effects = deepcopy(w.http["effects"])
        with CaptureQueriesContext(connection) as queries:
            metadata = current_metadata(deploy, w, store, reservation, iam)
        assert not any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries)
        assert w.http["effects"] == effects
        original = render_resources_for_deployment(deploy, prepared_gcp_identity=metadata)
        assert not any(r["kind"] in ("Namespace", "ServiceAccount") for r in original)
        work = next(r for r in original if r["kind"] == "Deployment")
        assert work["metadata"]["namespace"] == metadata.namespace
        assert work["spec"]["template"]["spec"]["serviceAccountName"] == metadata.service_account_name
        assert work["spec"]["template"]["spec"]["containers"][0]["image"].endswith(deploy.image_digest)
        annotations = work["spec"]["template"]["metadata"]["annotations"]
        assert "astrolift.io/app-env-secrets-digest" in annotations
        # This captured subscription is queued, so it must not become an applied binding.
        assert not any(key.startswith("astrolift.io/model-binding-") for key in annotations)
        fingerprints = prepared_controller_fingerprints(original, metadata)
        assert "synthetic-private-value" not in json.dumps([asdict(value) for value in fingerprints])
        w.medops_app.slug = "renamed-app"
        w.medops_app.save(update_fields=["slug"])
        w.org.slug = "renamed-organization"
        w.org.save(update_fields=["slug"])
        after = render_resources_for_deployment(deploy, prepared_gcp_identity=metadata)
        work = next(r for r in after if r["kind"] == "Deployment")
        assert work["metadata"]["namespace"] == metadata.namespace
        assert work["spec"]["template"]["spec"]["serviceAccountName"] == metadata.service_account_name
        # Rendering retention does not admit a changed producer logical subject plan.
        with pytest.raises(AppDeployError, match="TARGET_CHANGED"):
            render_resources_for_deployment(
                deploy, prepared_gcp_identity=replace(metadata, environment_guid=str(uuid4()))
            )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "change", ["iam_version", "iam_unknown", "token_revoked", "callback_false", "iam_callback_true"]
)
def test_actual_current_journal_or_authority_withdrawal_cannot_create_render_metadata(bridge, change):
    w = bridge
    deploy = deployment(w)
    with completed(w) as (store, reservation, iam):
        effects = deepcopy(w.http["effects"])
        kwargs = {}
        if change.startswith("iam_") and change != "iam_callback_true":
            GCPWorkloadIdentityJournal._unscoped.filter(guid=iam[1].journal_id).update(
                **({"version": 999} if change == "iam_version" else {"state": "UNKNOWN"})
            )
        elif change == "token_revoked":
            type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)
        elif change == "callback_false":
            kwargs["checkpoint"] = lambda context: False
        else:
            kwargs["iam_checkpoint"] = lambda context: True
        with pytest.raises((PreparationJournalError, PreparedIdentityRenderError, PermissionDenied)):
            current_metadata(deploy, w, store, reservation, iam, **kwargs)
        assert w.http["effects"] == effects


@pytest.mark.django_db(transaction=True)
def test_prepared_only_is_not_completed_iam_or_render_admission(bridge):
    w = bridge
    deploy = deployment(w)
    w.http["before_write"] = None
    with preparation_journal_mutex(w.target) as store:
        reservation = store.reserve(w.operation, checkpoint=admitted)
        prepare(w, store, reservation)
        with pytest.raises(PreparationJournalError):
            current_metadata(deploy, w, store, reservation, None)
