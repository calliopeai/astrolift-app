"""
Smoke test the authed-user → RoleBinding → resolver chain.

Logs in as the dev seed user via Django's test Client, hits the
identity GraphQL surface, and expects the *real* permission resolver
(astrolift_identity.permission_resolver) to allow the call. No
resolver swap, no shortcuts — proves the full chain works.

Run::

    docker compose -f docker/docker-compose.yaml exec -T astrolift-local \\
        python scripts/smoke_authed_rbac.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402

from astrolift_identity.models import Organization, RoleBinding  # noqa: E402

ENDPOINT = "/app/gql/config/"


def gql(client: Client, query: str, variables: dict | None = None) -> dict:
    resp = client.post(
        ENDPOINT,
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
    )
    return json.loads(resp.content)


def expect(label: str, ok: bool, detail=None) -> None:
    mark = "OK  " if ok else "FAIL"
    suffix = f" — {detail!r}" if detail else ""
    print(f"  [{mark}] {label}{suffix}", flush=True)
    if not ok:
        sys.exit(1)


CREATE = """
mutation Create($input: CreateOrganizationInput!) {
  createOrganization(input: $input) {
    ok errors { code message } data { id slug }
  }
}
"""

LIST = "{ astroliftOrganizations { slug name } }"


def main() -> None:
    User = get_user_model()
    user = User.objects.filter(username="dev@local.astrolift.net").first()
    if user is None:
        print("FAIL: run `manage.py seed_dev_identity` first")
        sys.exit(2)

    bindings = RoleBinding.objects.filter(user=user).count()
    print(f"  user pk={user.pk}  role bindings={bindings}")

    print("step 1: anonymous Client → deny")
    anon = Client()
    res = gql(anon, LIST)
    # Errors go through resolver-entry check, which raises through Strawberry
    # (queries don't have the MutationResult envelope). Either we get a
    # GraphQL error or an empty list — anything but unauthenticated success.
    is_denied = res.get("errors") is not None or res.get("data", {}).get("astroliftOrganizations") is None
    expect("denied", is_denied, str(res)[:120])

    print("step 2: login + RBAC-allowed list")
    authed = Client()
    ok = authed.login(username="dev@local.astrolift.net", password="dev")
    expect("login", ok)

    res = gql(authed, LIST)
    expect(
        "list returned data",
        res.get("data", {}).get("astroliftOrganizations") is not None,
        str(res)[:300],
    )
    slugs = [o["slug"] for o in res["data"]["astroliftOrganizations"]]
    expect("acme present", "acme" in slugs, slugs)

    print("step 3: authed createOrganization → ok=true via real RBAC")
    Organization.all_objects.filter(slug="rbac-smoke").delete()
    res = gql(
        authed,
        CREATE,
        {"input": {"name": "RBAC Smoke", "slug": "rbac-smoke"}},
    )
    payload = res["data"]["createOrganization"]
    expect("ok", payload["ok"] is True, str(payload.get("errors")))
    expect("slug", payload["data"]["slug"] == "rbac-smoke")
    Organization.all_objects.filter(slug="rbac-smoke").delete()

    print("\nall authed-RBAC checks passed.")


if __name__ == "__main__":
    main()
