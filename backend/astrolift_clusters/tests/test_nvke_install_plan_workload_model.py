from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError

from astrolift_clusters.models import (
    NvkeInstallPlan,
    NvkeInstallPlanCharts,
    NvkeInstallPlanTree,
    NvkeInstallPlanWorkload,
)
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_models import operation, site  # noqa: F401
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

pytestmark = pytest.mark.django_db


@pytest.fixture
def retained(site):  # noqa: F811
    original = operation(site)
    encrypted = encrypt_at_rest(b'{"contextBindings":{"private-item":{"contextDigest":"reviewed"}}}')
    fields = {
        "actor": original.actor,
        "source_binding": {"original": str(original.guid)},
        "request_digest": "c" * 64,
        "input_backend_kind": encrypted.backend_kind,
        "input_ciphertext": encrypted.backend_ref,
    }
    plan = NvkeInstallPlan.objects.create(original_operation=original, **fields)
    charts = NvkeInstallPlanCharts.objects.create(plan=plan, **fields)
    tree = NvkeInstallPlanTree.objects.create(charts=charts, **fields)
    return NvkeInstallPlanWorkload.objects.create(tree=tree, **fields)


def test_workload_intent_is_encrypted_and_parent_delete_is_protected(retained):
    row = retained
    raw = b'{"contextBindings":{"private-item":{"contextDigest":"reviewed"}}}'
    assert raw not in bytes(row.input_ciphertext)
    row.refresh_from_db()
    assert decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))) == raw
    with pytest.raises(ProtectedError):
        row.tree.delete()
    assert row.tree.workload_inventories.get().pk == row.pk


@pytest.mark.parametrize(
    "field,value",
    [
        ("guid", uuid4()),
        ("tree_id", 0),
        ("actor_id", 0),
        ("source_binding", {"foreign": True}),
        ("request_digest", "d" * 64),
        ("input_backend_kind", "foreign"),
        ("input_ciphertext", b"changed"),
    ],
)
def test_workload_original_intent_cannot_be_rewritten(retained, field, value):
    setattr(retained, field, value)
    with pytest.raises(ValueError):
        retained.save()


def test_prepared_workload_summary_and_status_are_immutable_and_soft_delete_keeps_uuid(retained):
    row = retained
    row.summary = {"captureDigest": "f" * 64, "continuationAuthorized": False}
    row.status = "prepared"
    row.save()
    row.summary = {"captureDigest": "e" * 64}
    with pytest.raises(ValueError):
        row.save()
    row.refresh_from_db()
    row.status = "preparing"
    with pytest.raises(ValueError):
        row.save()
    row.refresh_from_db()
    row.soft_delete()
    assert not NvkeInstallPlanWorkload.objects.filter(guid=row.guid).exists()
    assert NvkeInstallPlanWorkload.all_objects.filter(guid=row.guid).exists()
    with transaction.atomic(), pytest.raises(IntegrityError):
        NvkeInstallPlanWorkload.objects.create(
            guid=row.guid,
            tree=row.tree,
            actor=row.actor,
            source_binding=row.source_binding,
            request_digest=row.request_digest,
            input_backend_kind=row.input_backend_kind,
            input_ciphertext=row.input_ciphertext,
        )


def test_database_refuses_unknown_workload_status(retained):
    with transaction.atomic(), pytest.raises(IntegrityError):
        NvkeInstallPlanWorkload.objects.filter(pk=retained.pk).update(status="executed")


def test_equivalent_string_uuid_can_be_saved_but_a_different_uuid_still_refuses(retained):
    original = retained.guid
    retained.guid = str(original)
    retained.save(update_fields=["guid"])
    retained.refresh_from_db()
    assert retained.guid == original
    retained.guid = str(uuid4())
    with pytest.raises(ValueError):
        retained.save(update_fields=["guid"])
