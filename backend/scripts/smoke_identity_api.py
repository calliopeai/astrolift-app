"""
End-to-end smoke test for the identity GraphQL surface.

Drives ``createOrganization`` / ``softDeleteOrganization`` via Django's
test Client (no HTTP overhead) so the permission resolver swap and
the resolver call are in the same process. Run inside the container::

    docker compose -f docker/docker-compose.yaml exec -T astrolift-local \\
        python scripts/smoke_identity_api.py

Asserts:

* deny-by-default permission resolver refuses the call.
* a permissive resolver lets it through.
* duplicate slug returns CONFLICT.
* soft-delete works and the slug becomes re-claimable.
* the audit log captured the create + delete decisions.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client  # noqa: E402

from astrolift_identity.models import Organization  # noqa: E402
from astrolift_operations.models import AuditEvent  # noqa: E402
from core.permissions import register_permission_resolver  # noqa: E402

ENDPOINT = "/app/gql/config/"
client = Client()


def gql(query: str, variables: dict | None = None) -> dict:
    resp = client.post(
        ENDPOINT,
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
    )
    return json.loads(resp.content)


def expect(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        sys.exit(1)


CREATE = """
mutation Create($input: CreateOrganizationInput!) {
  createOrganization(input: $input) {
    ok
    errors { code message field }
    data { id slug name }
  }
}
"""

DELETE = """
mutation Del($input: SoftDeleteOrganizationInput!) {
  softDeleteOrganization(input: $input) {
    ok
    errors { code message }
    data { id deleted }
  }
}
"""


def main() -> None:
    # Clean slate
    Organization.all_objects.filter(slug="acme-smoke").delete()
    audit_before = AuditEvent.objects.count()

    print("step 1: deny-by-default")
    res = gql(CREATE, {"input": {"name": "Acme", "slug": "acme-smoke"}})
    payload = res["data"]["createOrganization"]
    expect("denied", payload["ok"] is False)
    expect("PERMISSION_DENIED code", payload["errors"][0]["code"] == "PERMISSION_DENIED")

    print("step 2: install permissive resolver and create")
    register_permission_resolver(lambda *a, **k: (True, "smoke"))

    res = gql(CREATE, {"input": {"name": "Acme Smoke", "slug": "acme-smoke"}})
    payload = res["data"]["createOrganization"]
    expect("create ok", payload["ok"] is True, str(payload.get("errors")))
    expect("slug echoed", payload["data"]["slug"] == "acme-smoke")
    org_guid = payload["data"]["id"]

    print("step 3: duplicate slug → CONFLICT")
    res = gql(CREATE, {"input": {"name": "Acme Smoke 2", "slug": "acme-smoke"}})
    payload = res["data"]["createOrganization"]
    expect("rejected", payload["ok"] is False)
    expect("CONFLICT code", payload["errors"][0]["code"] == "CONFLICT")

    print("step 4: soft-delete")
    res = gql(DELETE, {"input": {"id": org_guid}})
    payload = res["data"]["softDeleteOrganization"]
    expect("delete ok", payload["ok"] is True, str(payload.get("errors")))
    expect("deleted=true", payload["data"]["deleted"] is True)
    expect(
        "default manager hides it",
        Organization.objects.filter(slug="acme-smoke").exists() is False,
    )
    expect(
        "all_objects shows it",
        Organization.all_objects.filter(slug="acme-smoke").exists() is True,
    )

    print("step 5: slug reclaimable after delete")
    res = gql(CREATE, {"input": {"name": "Acme Reborn", "slug": "acme-smoke"}})
    payload = res["data"]["createOrganization"]
    expect("create ok", payload["ok"] is True, str(payload.get("errors")))
    expect("now two rows", Organization.all_objects.filter(slug="acme-smoke").count() == 2)

    print("step 6: audit log captured the calls")
    audit_after = AuditEvent.objects.count()
    expect(
        "audit grew",
        audit_after - audit_before >= 4,
        f"{audit_before} → {audit_after}",
    )
    actions = list(
        AuditEvent.objects.filter(action__in=("org.create", "org.delete"))
        .order_by("-occurred_at")
        .values_list("action", "decision")[:6]
    )
    print(f"  recent audit rows: {actions}")
    expect("contains org.create ALLOW", any(a == ("org.create", "ALLOW") for a in actions))
    expect("contains org.delete ALLOW", any(a == ("org.delete", "ALLOW") for a in actions))

    # Cleanup so reruns are deterministic
    Organization.all_objects.filter(slug="acme-smoke").delete()
    print("\nall smoke checks passed.")


if __name__ == "__main__":
    main()
