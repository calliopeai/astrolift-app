"""Ledger settlement for a stuck native journal (#2278): nothing observed is invented."""

from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import pytest
from gcp.identity_owned import (
    OwnedGrant,
    OwnedGrantLedger,
    PolicyIntent,
    PolicyOwnership,
    PolicySubmissionPhase,
    owned_ledger_from_payload,
    owned_ledger_payload,
)

from astrolift_services.gcp_native_journal_resolution import (
    JournalResolutionError,
    _hash,
    _iam,
    _preparation,
)

ENDPOINT = "projects/p/locations/us-central1/endpoints/1"
SECOND = "projects/p/locations/us-central1/endpoints/2"
KEPT = OwnedGrant("roles/aiplatform.predictor", "serviceAccount:app@p.iam.gserviceaccount.com")
LANDED = OwnedGrant("projects/p/roles/predict", "serviceAccount:app@p.iam.gserviceaccount.com")


def _iam_row(ledger):
    payload = owned_ledger_payload(ledger)
    return SimpleNamespace(ledger=payload, ledger_sha256=_hash(payload))


def test_iam_settlement_folds_possibly_landed_and_unfinished_removals_into_ownership():
    ledger = OwnedGrantLedger(
        "a" * 64,
        policies=(PolicyOwnership(ENDPOINT, (KEPT,)),),
        pending=(
            PolicyIntent(
                ENDPOINT,
                "b" * 64,
                "c" * 64,
                (KEPT, LANDED),
                str(uuid4()),
                PolicySubmissionPhase.SENT,
                "d" * 64,
                "e" * 64,
            ),
        ),
        removals=(PolicyOwnership(SECOND, (LANDED,)),),
    )
    payload, sha, dropped = _iam(_iam_row(ledger))
    settled = owned_ledger_from_payload(payload)
    # Owned, so the next reconcile removes any the current union no longer wants.
    assert settled.policies == (
        PolicyOwnership(ENDPOINT, tuple(sorted((KEPT, LANDED)))),
        PolicyOwnership(SECOND, (LANDED,)),
    )
    assert settled.pending == () and settled.removals == ()
    assert settled.context_sha256 == ledger.context_sha256 and sha == _hash(payload)
    assert dropped == [{"resource": ENDPOINT, "phase": "SENT"}, {"resource": SECOND, "phase": "REMOVAL"}]


def test_iam_settlement_refuses_a_ledger_that_does_not_match_its_hash():
    row = _iam_row(OwnedGrantLedger("a" * 64))
    row.ledger_sha256 = "f" * 64
    with pytest.raises(JournalResolutionError, match="JOURNAL_INTEGRITY_FAILURE"):
        _iam(row)


def test_preparation_settlement_drops_intents_and_keeps_recorded_uids():
    from gcp.gke_identity_preparation import (
        GKEPreparationLedger,
        PreparationIntent,
        PreparationPhase,
        PreparedObject,
    )

    namespace = PreparedObject("Namespace", "/api/v1/namespaces/app", str(uuid4()), "11", str(uuid4()))
    sent = PreparationIntent(
        str(uuid4()),
        str(uuid4()),
        "ServiceAccount",
        "/api/v1/namespaces/app/serviceaccounts/app",
        "POST",
        "1" * 64,
        "2" * 64,
        phase=PreparationPhase.SENT,
    )
    ledger = GKEPreparationLedger("3" * 64, (namespace,), (sent,))
    row = SimpleNamespace(ledger={"schema_version": 1, **asdict(ledger)}, ledger_sha256=ledger.sha256)
    payload, sha, dropped = _preparation(row)
    assert payload["objects"] == [asdict(namespace)] and payload["pending"] == []
    assert sha == GKEPreparationLedger("3" * 64, (namespace,)).sha256
    assert dropped == [{"kind": "ServiceAccount", "path": sent.path, "method": "POST", "phase": "SENT"}]
    row.ledger_sha256 = "0" * 64
    with pytest.raises(JournalResolutionError, match="JOURNAL_INTEGRITY_FAILURE"):
        _preparation(row)
