from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError

from astrolift_clusters.models import (
    NvkeInstallPlan,
    NvkeInstallPlanCharts,
    NvkeInstallPlanContext,
    NvkeInstallPlanTree,
    NvkeInstallPlanVerification,
)
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_models import operation, site  # noqa: F401
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

pytestmark = pytest.mark.django_db


@pytest.fixture
def retained(site):  # noqa: F811
    original = operation(site)
    encrypted = encrypt_at_rest(b'{"revision":"9007199254740993","upgrade":true}')
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
    context = NvkeInstallPlanContext.objects.create(tree=tree, **fields)
    return NvkeInstallPlanVerification.objects.create(context=context, **fields)


def test_verification_intent_is_encrypted_and_parent_delete_is_protected(retained):
    row = retained
    raw = b'{"revision":"9007199254740993","upgrade":true}'
    assert raw not in bytes(row.input_ciphertext)
    row.refresh_from_db()
    assert decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))) == raw
    with pytest.raises(ProtectedError):
        row.context.delete()
    assert row.context.render_verifications.get().pk == row.pk


@pytest.mark.parametrize(
    "field,value",
    [
        ("guid", uuid4()),
        ("context_id", 0),
        ("actor_id", 0),
        ("source_binding", {"foreign": True}),
        ("request_digest", "d" * 64),
        ("input_backend_kind", "foreign"),
        ("input_ciphertext", b"changed"),
    ],
)
def test_verification_original_intent_cannot_be_rewritten(retained, field, value):
    setattr(retained, field, value)
    with pytest.raises(ValueError):
        retained.save()


def test_prepared_verification_summary_and_status_are_immutable_and_soft_delete_keeps_uuid(retained):
    row = retained
    row.summary = {"verificationDigest": "f" * 64, "continuationAuthorized": False}
    row.status = "prepared"
    row.save()
    row.summary = {"verificationDigest": "e" * 64}
    with pytest.raises(ValueError):
        row.save()
    row.refresh_from_db()
    row.status = "preparing"
    with pytest.raises(ValueError):
        row.save()
    row.refresh_from_db()
    row.soft_delete()
    assert not NvkeInstallPlanVerification.objects.filter(guid=row.guid).exists()
    assert NvkeInstallPlanVerification.all_objects.filter(guid=row.guid).exists()
    with transaction.atomic(), pytest.raises(IntegrityError):
        NvkeInstallPlanVerification.objects.create(
            guid=row.guid,
            context=row.context,
            actor=row.actor,
            source_binding=row.source_binding,
            request_digest=row.request_digest,
            input_backend_kind=row.input_backend_kind,
            input_ciphertext=row.input_ciphertext,
        )


def test_database_refuses_unknown_verification_status(retained):
    with transaction.atomic(), pytest.raises(IntegrityError):
        NvkeInstallPlanVerification.objects.filter(pk=retained.pk).update(status="executed")
