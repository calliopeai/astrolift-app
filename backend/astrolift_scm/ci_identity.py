"""Repository CI names retain the registered app identity across slug edits."""

from __future__ import annotations

import hashlib
import json
import uuid


def github_ci_identity(app) -> str:
    try:
        return uuid.UUID(str(app.guid)).hex
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Managed GitHub CI requires a registered app GUID") from exc


def github_ci_secret_name(app, name: str) -> str:
    return f"{name}_{github_ci_identity(app).upper()}"


_OWNER_PREFIX = "# astrolift-ci-owner: "


def github_ci_owner(app) -> dict:
    return {
        "app_guid": str(uuid.UUID(github_ci_identity(app))),
        "organization_id": getattr(app, "organization_id", None),
        "source_kind": app.source_kind,
        "source_repo": app.source_repo,
    }


def github_ci_owner_line(app) -> str:
    return _OWNER_PREFIX + json.dumps(github_ci_owner(app), sort_keys=True, separators=(",", ":")) + "\n"


def owns_unchanged_github_workflow(app, content: str) -> bool:
    from astrolift_scm.ci_templates import parse_stamp

    expected = github_ci_owner(app)
    if not expected["organization_id"] or expected["source_kind"] != "github":
        return False
    owners = [
        line.removeprefix(_OWNER_PREFIX) for line in content.splitlines() if line.startswith(_OWNER_PREFIX)
    ]
    if len(owners) != 1:
        return False
    try:
        if json.loads(owners[0]) != expected:
            return False
    except (ValueError, TypeError):
        return False
    stamp = parse_stamp(content)
    return (
        stamp.version is not None
        and stamp.declared_sha is not None
        and len(stamp.declared_sha) in {16, 64}
        and hashlib.sha256(stamp.body_without_stamp.encode()).hexdigest().startswith(stamp.declared_sha)
    )
