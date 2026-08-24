"""Expired audit events are archived, and nothing is deleted (#1594).

The issue records a contradiction: the UI says "retained for N days", and
migration 0003 binds a `BEFORE UPDATE OR DELETE` trigger to
`astrolift_operations_auditevent` so Postgres refuses the DELETE. Resolving
that is a compliance-posture decision and is not made here.

What is built here is the half that needs no decision. Archiving weakens
nothing, is useful under two of the three options and harmless under the
third, and is a prerequisite for the one the issue calls most likely. The
delete half is deliberately absent: adding a privileged DELETE path would
settle the question by making it.

The load-bearing assertion in this file is the last one -- that the trigger
still refuses DELETE after all of this.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from astrolift_operations.audit_archive import archive_expired_audit_events
from astrolift_operations.models import AuditArchive, AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def org(django_user_model):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Acme", slug="acme", audit_export_enabled=True)


@pytest.fixture
def uploads(monkeypatch):
    """Capture what reaches the blob store, without a blob store."""
    sent: list[tuple[str, bytes]] = []

    class _Driver:
        def upload(self, key, data, *, content_type="application/octet-stream"):
            sent.append((key, data))

    import core.blob_store_resolution as res

    monkeypatch.setattr(res, "install_s3_driver", lambda *, purpose: _Driver())
    return sent


def _event(org, *, days_ago: int) -> AuditEvent:
    """Create an AuditEvent dated in the past.

    `occurred_at` is `auto_now_add`, and the append-only trigger refuses
    UPDATE as well as DELETE, so neither passing it to `create()` nor fixing
    it afterwards works. Suspending the flag for the insert is the only way
    to build a row past retention, which is itself a small demonstration that
    the trigger does what it claims.
    """
    field = AuditEvent._meta.get_field("occurred_at")
    field.auto_now_add = False
    try:
        return AuditEvent.objects.create(
            organization=org,
            occurred_at=timezone.now() - dt.timedelta(days=days_ago),
            actor_kind="user",
            actor_id="u-1",
            actor_display="ada@acme.test",
            action="app.deploy",
            decision="allow",
            target_kind="app",
            target_id="hello",
            target_slug="hello-app",
            data={"env": "prod"},
        )
    finally:
        field.auto_now_add = True


def test_expired_events_are_written_to_the_blob_store(org, uploads):
    _event(org, days_ago=400)

    result = archive_expired_audit_events(org, now=timezone.now(), retention_days=365)

    assert result["rows"] == 1
    assert len(uploads) == 1
    key, payload = uploads[0]
    assert key.startswith("audit/acme/")
    assert b"app.deploy" in payload


def test_events_inside_the_window_are_left_alone(org, uploads):
    _event(org, days_ago=10)

    result = archive_expired_audit_events(org, now=timezone.now(), retention_days=365)

    assert result["rows"] == 0
    assert uploads == []


def test_an_org_that_has_not_opted_in_is_skipped(org, uploads):
    """Off by default. An archive leaves the platform's storage for wherever
    the install points its bucket, so it is an operator's choice."""
    org.audit_export_enabled = False
    org.save(update_fields=["audit_export_enabled"])
    _event(org, days_ago=400)

    result = archive_expired_audit_events(org, now=timezone.now(), retention_days=365)

    assert result["rows"] == 0
    assert "not enabled" in result["skipped"]
    assert uploads == []


def test_no_blob_store_is_skipped_rather_than_failed(org, monkeypatch):
    """An install with no bucket is a supported state, not an error. The
    activity's contract is the count it returns."""
    import core.blob_store_resolution as res

    monkeypatch.setattr(res, "install_s3_driver", lambda *, purpose: None)
    _event(org, days_ago=400)

    result = archive_expired_audit_events(org, now=timezone.now(), retention_days=365)

    assert result["rows"] == 0
    assert "no blob store" in result["skipped"]


def test_the_archive_is_hash_chained(org, uploads):
    """The property that makes an archive worth more than a backup: tampering
    with one line invalidates every line after it."""
    from astrolift_operations.retention import verify_chain

    for days in (400, 401, 402):
        _event(org, days_ago=days)

    archive_expired_audit_events(org, now=timezone.now(), retention_days=365)

    _, payload = uploads[0]
    assert verify_chain(payload)

    tampered = payload.replace(b"app.deploy", b"app.delete", 1)
    assert not verify_chain(tampered)


def test_a_row_records_the_digest_for_an_auditor(org, uploads):
    _event(org, days_ago=400)

    archive_expired_audit_events(org, now=timezone.now(), retention_days=365)

    row = AuditArchive.objects.get(organization=org)
    assert row.row_count == 1
    assert len(row.sha256) == 64
    assert row.object_key == uploads[0][0]
    assert row.covered_through is not None


def test_archiving_does_not_delete_anything(org, uploads):
    """The whole point. Archiving is the half that needs no decision; the
    delete half is #1594's and is not made here."""
    _event(org, days_ago=400)

    archive_expired_audit_events(org, now=timezone.now(), retention_days=365)

    assert AuditEvent.objects.filter(organization=org).count() == 1


def test_archiving_alone_deletes_nothing(org, uploads):
    """The archive and the delete stay separable.

    This test previously asserted the trigger refused DELETE outright. #1594
    was then decided as export-then-delete, so that is no longer true and the
    assertion moved: see `test_an_ordinary_delete_is_still_refused` and
    `test_update_is_still_refused_even_during_a_sweep`, which hold the parts
    of the guarantee that survived.

    What is held here is that archiving is not deleting. A caller that only
    archives must leave every row standing, so an operator can turn the
    archive on and observe it before anything is removed.
    """
    event = _event(org, days_ago=400)

    archive_expired_audit_events(org, now=timezone.now(), retention_days=365)

    assert AuditEvent.objects.filter(pk=event.pk).exists()


def test_the_exporter_reads_only_fields_the_model_has():
    """The guard for the class of bug that produced this whole change.

    `_row_to_record`'s audit branch read `resource_kind`, `resource_id` and
    `payload`. `AuditEvent` has `target_kind`, `target_id` and `data`. So the
    exporter raised AttributeError on the first real row it was ever handed,
    and `test_retention.py` stayed green throughout because its `_FakeAudit`
    was shaped like the exporter rather than like the model.

    Two things could drift again -- the exporter, or the fake -- so this
    checks the exporter against the real model. A fake cannot satisfy it.
    """
    from astrolift_operations.retention import _row_to_record

    class _Probe:
        """Answers only for concrete AuditEvent fields; raises otherwise,
        exactly as a real instance would."""

        def __init__(self):
            self._names = {f.name for f in AuditEvent._meta.get_fields() if hasattr(f, "attname")}
            self._names |= {f.attname for f in AuditEvent._meta.concrete_fields}
            self.read: set[str] = set()

        def __getattr__(self, name):
            names = object.__getattribute__(self, "_names")
            if name not in names:
                raise AttributeError(f"AuditEvent has no field {name!r}")
            object.__getattribute__(self, "read").add(name)
            return {} if name in {"data", "reasoning"} else "x"

    probe = _Probe()
    probe.occurred_at = timezone.now()

    record = _row_to_record(probe, stream="audit")

    assert record["stream"] == "audit"
    assert "target_kind" in record
    assert "resource_kind" not in record


# ---- the delete half (#1594, decided: export then delete) ----------------


def test_archived_events_are_deleted(org, uploads):
    """The decision, working. Archive first, then remove."""
    from astrolift_operations.audit_archive import prune_archived_audit_events

    _event(org, days_ago=400)

    result = archive_expired_audit_events(org, now=timezone.now(), retention_days=365)
    deleted = prune_archived_audit_events(result["archived_pks"])

    assert deleted == 1
    assert AuditEvent.objects.filter(organization=org).count() == 0


def test_only_the_rows_that_were_archived_are_deleted(org, uploads):
    """The ordering guarantee, and the reason the pruner takes explicit pks
    rather than re-querying: an event that ages past the cutoff between the
    archive and the delete would otherwise be removed with no copy of it."""
    from astrolift_operations.audit_archive import prune_archived_audit_events

    archived = _event(org, days_ago=400)
    result = archive_expired_audit_events(org, now=timezone.now(), retention_days=365)

    # Arrives after the archive, also past retention.
    latecomer = _event(org, days_ago=500)

    prune_archived_audit_events(result["archived_pks"])

    assert not AuditEvent.objects.filter(pk=archived.pk).exists()
    assert AuditEvent.objects.filter(pk=latecomer.pk).exists()


def test_nothing_is_deleted_when_the_archive_was_skipped(org, monkeypatch):
    """No path where the delete runs and the export did not. An install with
    no blob store keeps its events."""
    import core.blob_store_resolution as res
    from astrolift_operations.audit_archive import prune_archived_audit_events

    monkeypatch.setattr(res, "install_s3_driver", lambda *, purpose: None)
    _event(org, days_ago=400)

    result = archive_expired_audit_events(org, now=timezone.now(), retention_days=365)
    deleted = prune_archived_audit_events(result.get("archived_pks", []))

    assert deleted == 0
    assert AuditEvent.objects.filter(organization=org).count() == 1


def test_an_ordinary_delete_is_still_refused(org, uploads):
    """The gate is not a hole. Only a transaction that opts in may delete,
    and nothing outside the sweep does."""
    from django.db import InternalError, transaction

    event = _event(org, days_ago=400)

    with pytest.raises(InternalError), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).delete()


def test_update_is_still_refused_even_during_a_sweep(org, uploads):
    """The line the decision does not cross.

    Retention is about how long a record is kept, never about editing one.
    An audit trail that can be rewritten is worth nothing, and the compliance
    argument for deletion does not extend to mutation -- so the trigger
    honours the gate for DELETE only.
    """
    from django.db import InternalError, connection, transaction

    event = _event(org, days_ago=400)

    with pytest.raises(InternalError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL astrolift.retention_sweep = 'on'")
        AuditEvent.objects.filter(pk=event.pk).update(action="tampered")


def test_the_gate_does_not_leak_past_its_transaction(org, uploads):
    """A gate that outlived its own function would leave the next caller on
    that connection able to delete audit events without knowing it.

    This failed on the first attempt, which is why the gate is now closed
    explicitly in a `finally` rather than left to `SET LOCAL` to unwind.
    `SET LOCAL` is scoped to the *outermost* transaction, not the nearest
    `atomic()` block, so under any outer transaction -- which is what this
    test runs inside, and what `ATOMIC_REQUESTS` or a nested `atomic()`
    would produce in production -- it stayed open for everything after.
    """
    from django.db import InternalError, transaction

    from astrolift_operations.audit_archive import prune_archived_audit_events

    _event(org, days_ago=400)
    result = archive_expired_audit_events(org, now=timezone.now(), retention_days=365)
    prune_archived_audit_events(result["archived_pks"])

    survivor = _event(org, days_ago=400)
    with pytest.raises(InternalError), transaction.atomic():
        AuditEvent.objects.filter(pk=survivor.pk).delete()


def test_events_inside_the_window_survive_the_sweep(org, uploads):
    from astrolift_operations.audit_archive import prune_archived_audit_events

    recent = _event(org, days_ago=10)
    _event(org, days_ago=400)

    result = archive_expired_audit_events(org, now=timezone.now(), retention_days=365)
    prune_archived_audit_events(result["archived_pks"])

    assert AuditEvent.objects.filter(pk=recent.pk).exists()


# ---- the wiring, not just the two halves --------------------------------


def test_the_sweep_deletes_only_what_it_archived(org, uploads):
    """Drives `_archive_org`, the code that actually runs on the schedule.

    Added because a mutation survived: the two halves were well covered on
    their own, and nothing exercised the call between them. Replacing the
    sweep's `result["archived_pks"]` with a fresh query over the org's events
    kept every other test green while deleting rows that had never been
    written anywhere.

    Testing the parts and not the seam is how export-then-delete would have
    become delete-and-also-export-sometimes.
    """
    from astrolift_workflows.activities.scheduled import _archive_org

    expired = _event(org, days_ago=400)
    recent = _event(org, days_ago=10)

    _archive_org(org.pk, now=timezone.now(), retention_days=365)

    assert not AuditEvent.objects.filter(pk=expired.pk).exists()
    assert AuditEvent.objects.filter(pk=recent.pk).exists()
    assert uploads, "the sweep deleted without archiving"


def test_the_sweep_deletes_nothing_when_there_is_no_blob_store(org, monkeypatch):
    """The mutation this was written for. No archive, no delete -- ever."""
    import core.blob_store_resolution as res
    from astrolift_workflows.activities.scheduled import _archive_org

    monkeypatch.setattr(res, "install_s3_driver", lambda *, purpose: None)
    expired = _event(org, days_ago=400)

    _archive_org(org.pk, now=timezone.now(), retention_days=365)

    assert AuditEvent.objects.filter(pk=expired.pk).exists()


def test_the_sweep_deletes_nothing_for_an_org_that_has_not_opted_in(org, uploads):
    org.audit_export_enabled = False
    org.save(update_fields=["audit_export_enabled"])
    expired = _event(org, days_ago=400)

    from astrolift_workflows.activities.scheduled import _archive_org

    _archive_org(org.pk, now=timezone.now(), retention_days=365)

    assert AuditEvent.objects.filter(pk=expired.pk).exists()
    assert uploads == []
