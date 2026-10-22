"""Native teardown of an Endpoint-only GKE app's owned IAM grants (#2278).

Deregistering or tearing down an app reconciles each of its IAM journals to the
empty union: every Endpoint prediction grant and every Workload Identity binding
on the GSA that the journal owns is removed. Bindings it does not own stay,
another app's grant on the same Endpoint and every conditional binding included.
Nothing is created or deleted, and the GSA itself remains.

The removal is one operation on the IAM journal, under the executor's
preparation and IAM mutexes, so it never races a native configuration. It
refuses before any native call while a Deployment's IAM operation is
unresolved. The next attempt resumes an interrupted removal, and a SENT write
resolves only by its exact after-hash. Until every owned grant is observed gone
the removal stays pending, and teardown keeps the app's rows and services.
"""

import hashlib
import json
from contextlib import ExitStack
from dataclasses import asdict, fields
from uuid import uuid4

from django.db import connection
from gcp.identity_owned import NativeIdentityContext, desired_owned_union_sha256, owned_ledger_from_payload

from astrolift_registry.models import RegisteredApp
from astrolift_services.gcp_native_journal_resolution import JournalResolutionError, _lock_id, _mutex
from astrolift_services.gcp_workload_identity_journal import (
    JournalTarget,
    KSAIdentity,
    OperationIdentity,
    journal_mutex,
    removal_authority_sha256,
)
from astrolift_services.models import GCPWorkloadIdentityJournal
from astrolift_workflows.native_endpoint_configuration import NativeConfigurationPorts
from core.cluster_credentials import credential_for_cluster

_MAX_JOURNALS = 64
_SETTLED = ("OBSERVED", "RESOLVED")


class NativeIdentityRemovalError(ValueError):
    """Fixed reasons only; native text never leaves."""


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _credential_sha256(cluster):
    try:
        return _hash(asdict(credential_for_cluster(cluster)))
    except Exception:
        raise NativeIdentityRemovalError("CURRENT_IDENTITY_SOURCE_UNAVAILABLE") from None


def _target(journal):
    """The journal's original target, exactly as it was committed."""
    saved = journal.target_snapshot
    try:
        target = JournalTarget(
            **{
                item.name: saved[item.name] for item in fields(JournalTarget) if item.name != "ksa_identities"
            },
            ksa_identities=tuple(KSAIdentity(**item) for item in saved["ksa_identities"]),
        )
    except (KeyError, TypeError, ValueError):
        raise NativeIdentityRemovalError("ORIGINAL_TARGET_INTEGRITY_FAILED") from None
    owners = (
        str(journal.organization.guid),
        str(journal.registered_app.guid),
        str(journal.tenant_cluster.guid),
        str(journal.provider_plugin.guid),
    )
    if (
        json.loads(json.dumps(target.payload)) != saved
        or target.sha256 != journal.target_sha256
        or target.context_sha256 != journal.context_sha256
        or (target.organization_id, target.app_id, target.cluster_id, target.provider_id) != owners
    ):
        raise NativeIdentityRemovalError("ORIGINAL_TARGET_INTEGRITY_FAILED")
    return target


def _identity(target, cluster):
    """The original GSA, pinned by its unique ID, under the current declared credential."""
    try:
        credential = credential_for_cluster(cluster)
        identity = NativeIdentityContext(
            target.organization_id,
            target.app_id,
            target.cluster_id,
            target.project_id,
            target.project_number,
            target.region,
            target.gsa_id,
            target.gsa_unique_id,
            credential,
        )
    except Exception:
        raise NativeIdentityRemovalError("CURRENT_IDENTITY_SOURCE_UNAVAILABLE") from None
    if (
        identity.fingerprint != target.context_sha256
        or _hash(asdict(credential)) != target.credential_declaration_sha256
    ):
        raise NativeIdentityRemovalError("CURRENT_IDENTITY_SOURCE_UNAVAILABLE")
    return identity


def _ledger(row):
    if _hash(row.ledger) != row.ledger_sha256:
        raise NativeIdentityRemovalError("JOURNAL_INTEGRITY_FAILURE")
    try:
        return owned_ledger_from_payload(row.ledger)
    except ValueError:
        raise NativeIdentityRemovalError("JOURNAL_INTEGRITY_FAILURE") from None


def _remove(journal, workflow_id, execution_id, ports):
    target = _target(journal)
    with ExitStack() as mutexes:
        # The executor's order. A live configuration holds both for its whole run.
        try:
            mutexes.enter_context(
                _mutex(_lock_id("astrolift-gcp-gke-preparation-v1", target.app_id, target.cluster_id))
            )
        except JournalResolutionError:
            raise NativeIdentityRemovalError("PREPARATION_BUSY") from None
        store = mutexes.enter_context(journal_mutex(target))
        row = GCPWorkloadIdentityJournal.all_objects.select_related("tenant_cluster").get(pk=journal.pk)
        ledger = _ledger(row)
        held = {(item.resource, grant) for item in ledger.policies for grant in item.grants} | {
            (item.resource, grant) for item in ledger.pending for grant in item.owned_after
        }
        # A settled journal never carries removal obligations.
        if row.state in _SETTLED and not held:
            return {"cluster": target.cluster_id, "removed": 0}
        identity = _identity(target, row.tenant_cluster)
        authority = removal_authority_sha256(target.organization_id, target.app_id, target.cluster_id)
        if row.authority_reference_sha256 == authority and row.state not in _SETTLED:
            # This app's own interrupted removal: resume it, never start another.
            operation = OperationIdentity(
                str(row.operation_id),
                row.workflow_id,
                row.execution_id,
                row.desired_revision,
                row.desired_union_sha256,
                row.authority_reference_sha256,
            )
            expected_version = None
        elif row.state in ("SENT", "UNKNOWN"):
            raise NativeIdentityRemovalError("PRIOR_SEND_UNRESOLVED")
        elif row.state not in _SETTLED:
            raise NativeIdentityRemovalError("PRIOR_OPERATION_INCOMPLETE")
        else:
            union = desired_owned_union_sha256(identity, (), service_account_uids=())
            operation = OperationIdentity(
                str(uuid4()), workflow_id, execution_id, row.desired_revision + 1, union, authority
            )
            expected_version = row.version

        def admitted(checkpoint):
            if checkpoint.operation != operation or checkpoint.target != target:
                raise NativeIdentityRemovalError("REMOVAL_ACCEPTANCE_CHANGED")
            if checkpoint.app.provisioning_status != RegisteredApp.ProvisioningStatus.TEARING_DOWN:
                raise NativeIdentityRemovalError("APP_NOT_TEARING_DOWN")
            if _credential_sha256(checkpoint.cluster) != target.credential_declaration_sha256:
                raise NativeIdentityRemovalError("CURRENT_IDENTITY_SOURCE_UNAVAILABLE")

        reservation = store.reserve(operation, checkpoint=admitted, expected_version=expected_version)
        with ports.iam(identity) as driver:
            result = driver.reconcile(
                (),
                service_account_uids=(),
                ledger=store.read(reservation, checkpoint=admitted),
                checkpoint=lambda: store.validate_current(reservation, checkpoint=admitted),
                persist=lambda value: store.persist_ledger(reservation, value, checkpoint=admitted),
                submission_hook=lambda value: store.commit_submission(
                    reservation, value, checkpoint=admitted
                ),
            )
        store.record_result(reservation, result.receipt, checkpoint=admitted, completed=True)
        return {"cluster": target.cluster_id, "removed": len(held)}


def remove_native_endpoint_identity(registered_app_id, *, workflow_id, execution_id, ports=None):
    """Revoke every grant the app's IAM journals own, one cluster at a time."""
    ports = ports or NativeConfigurationPorts()
    if type(ports) is not NativeConfigurationPorts:
        raise NativeIdentityRemovalError("PROTECTED_NATIVE_INPUT_REQUIRED")
    if connection.in_atomic_block or not connection.get_autocommit():
        raise NativeIdentityRemovalError("REMOVAL_REQUIRES_COMMITTED_DATABASE")
    journals = tuple(
        GCPWorkloadIdentityJournal.all_objects.filter(registered_app_id=registered_app_id)
        .select_related("organization", "registered_app", "tenant_cluster", "provider_plugin")
        .order_by("tenant_cluster__guid", "pk")[: _MAX_JOURNALS + 1]
    )
    if len(journals) > _MAX_JOURNALS:
        raise NativeIdentityRemovalError("REMOVAL_BOUND_EXCEEDED")
    return [_remove(journal, workflow_id, execution_id, ports) for journal in journals]
