"""Does an applied secret-change proposal account for this ``[env]`` edit (#1759)?

When an app requires secret approval (#488), setAppSecret / rotateAppSecret
/ deleteAppSecret create a ``SecretChangeProposal``; once approved,
``apply_proposal`` writes the change into ``manifest_raw_staged``. For an app
with a source repo that buffer then goes through pushManifestToRepo's PR.
For an app with none, ``applyStagedManifest`` is the only way to move it
into ``manifest_raw``, and it must tell an approved change from one that
``updateManifest`` staged with no proposal at all.

A change to a key is approved when the latest applied SET/DELETE proposal
for that key produced exactly that result: SET with the same value, or
DELETE of a key the edit removes. A later proposal for the key supersedes
an earlier one, so re-staging a value that a newer approval replaced does
not match.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from astrolift_services.models import SecretChangeProposal


@dataclasses.dataclass(frozen=True, slots=True)
class ProposalMatch:
    # Keys no applied proposal accounts for, in the order they were asked about.
    unapproved: list[str]
    # Guids of the applied proposals that account for the rest.
    proposal_ids: list[str]


def match_applied_proposals(app, changes: Mapping[str, Any]) -> ProposalMatch:
    """``changes`` maps each changed ``[env]`` key to its value after the
    edit, ``None`` when the edit removes it."""
    latest: dict[str, SecretChangeProposal] = {}
    applied = SecretChangeProposal.objects.filter(
        registered_app=app,
        status=SecretChangeProposal.Status.APPLIED.value,
        op__in=(SecretChangeProposal.Op.SET.value, SecretChangeProposal.Op.DELETE.value),
        payload__key__in=list(changes),
        deleted_at__isnull=True,
    ).order_by("-applied_at", "-pk")
    for proposal in applied:
        latest.setdefault(str((proposal.payload or {}).get("key")), proposal)

    unapproved: list[str] = []
    proposal_ids: list[str] = []
    for key, after in changes.items():
        proposal = latest.get(key)
        if proposal is None:
            matched = False
        elif proposal.op == SecretChangeProposal.Op.SET.value:
            # apply_proposal writes str(payload value) as a TOML string.
            matched = isinstance(after, str) and after == str((proposal.payload or {}).get("value") or "")
        else:
            matched = after is None
        if matched:
            proposal_ids.append(str(proposal.guid))
        else:
            unapproved.append(key)
    return ProposalMatch(unapproved=unapproved, proposal_ids=proposal_ids)
