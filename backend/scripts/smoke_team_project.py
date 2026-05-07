"""
Smoke the Team + Project mutation chain end-to-end.

Logs in as the seeded dev user, creates a fresh org for the run,
exercises the create/update/softDelete flow on Team + Project,
verifies CONFLICT on duplicates and NOT_FOUND on missing rows.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402

from astrolift_identity.models import (  # noqa: E402
    Member,
    Organization,
    Project,
    Role,
    RoleBinding,
    Team,
)

ENDPOINT = "/app/gql/config/"


class GQL:
    """Per-test client wrapper that pins an X-Astrolift-Organization header.

    The middleware needs an org to scope the resolver against; pinning
    it here keeps each request explicit instead of relying on the
    single-membership fallback (which doesn't work once the user is in
    multiple orgs).
    """

    def __init__(self, client: Client, org_guid: str) -> None:
        self.client = client
        self.org_guid = org_guid

    def __call__(self, query: str, variables: dict | None = None) -> dict:
        resp = self.client.post(
            ENDPOINT,
            data=json.dumps({"query": query, "variables": variables or {}}),
            content_type="application/json",
            HTTP_X_ASTROLIFT_ORGANIZATION=self.org_guid,
        )
        return json.loads(resp.content)


def expect(label: str, ok: bool, detail=None) -> None:
    mark = "OK  " if ok else "FAIL"
    suffix = f" — {detail!r}" if detail else ""
    print(f"  [{mark}] {label}{suffix}", flush=True)
    if not ok:
        sys.exit(1)


CREATE_TEAM = """
mutation T($input: CreateTeamInput!) {
  createTeam(input: $input) { ok errors { code message field } data { id slug name } }
}
"""

UPDATE_TEAM = """
mutation U($input: UpdateTeamInput!) {
  updateTeam(input: $input) { ok errors { code message } data { id name slug } }
}
"""

DELETE_TEAM = """
mutation D($input: SoftDeleteByGuidInput!) {
  softDeleteTeam(input: $input) { ok errors { code } data { id deleted } }
}
"""

CREATE_PROJECT = """
mutation P($input: CreateProjectInput!) {
  createProject(input: $input) { ok errors { code message field } data { id slug name } }
}
"""

UPDATE_PROJECT = """
mutation Q($input: UpdateProjectInput!) {
  updateProject(input: $input) { ok errors { code message } data { id name slug } }
}
"""

DELETE_PROJECT = """
mutation R($input: SoftDeleteByGuidInput!) {
  softDeleteProject(input: $input) { ok errors { code } data { id deleted } }
}
"""


def _setup_isolated_org() -> tuple[Organization, str]:
    """Create a per-run org so re-runs don't collide.

    Bind the dev user with org_owner so the resolver allows everything.
    """
    User = get_user_model()
    user = User.objects.get(username="dev@local.astrolift.net")

    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"Smoke {suffix}", slug=f"smoke-{suffix}")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=org.id, is_active=True)

    org_owner = Role.objects.get(slug="org_owner", is_system=True, organization=None)
    RoleBinding.objects.create(user=user, role=org_owner, scope_kind="ORG", scope_id=org.id)
    return org, suffix


def main() -> None:
    org, suffix = _setup_isolated_org()
    print(f"  isolated org: slug={org.slug} guid={org.guid}")

    authed = Client()
    assert authed.login(username="dev@local.astrolift.net", password="dev")
    gql = GQL(authed, str(org.guid))

    print("step 1: createTeam")
    res = gql(
        CREATE_TEAM,
        {"input": {"organizationId": str(org.guid), "name": "Backend", "slug": "backend"}},
    )
    payload = res["data"]["createTeam"]
    expect("ok", payload["ok"], payload.get("errors"))
    team_guid = payload["data"]["id"]

    print("step 2: createTeam duplicate slug → CONFLICT")
    res = gql(
        CREATE_TEAM,
        {"input": {"organizationId": str(org.guid), "name": "Backend2", "slug": "backend"}},
    )
    payload = res["data"]["createTeam"]
    expect("conflict", payload["ok"] is False)
    expect("CONFLICT code", payload["errors"][0]["code"] == "CONFLICT")

    print("step 3: createTeam unknown org → NOT_FOUND")
    bogus = "00000000-0000-7000-8000-000000000000"
    res = gql(
        CREATE_TEAM,
        {"input": {"organizationId": bogus, "name": "X", "slug": "x"}},
    )
    payload = res["data"]["createTeam"]
    expect("not found", payload["ok"] is False)
    expect("NOT_FOUND code", payload["errors"][0]["code"] == "NOT_FOUND")

    print("step 4: updateTeam")
    res = gql(UPDATE_TEAM, {"input": {"id": team_guid, "name": "Backend renamed"}})
    payload = res["data"]["updateTeam"]
    expect("ok", payload["ok"], payload.get("errors"))
    expect("name updated", payload["data"]["name"] == "Backend renamed")

    print("step 5: createProject")
    res = gql(
        CREATE_PROJECT,
        {"input": {"teamId": team_guid, "name": "Gateway", "slug": "gateway"}},
    )
    payload = res["data"]["createProject"]
    expect("ok", payload["ok"], payload.get("errors"))
    project_guid = payload["data"]["id"]

    print("step 6: createProject duplicate → CONFLICT")
    res = gql(
        CREATE_PROJECT,
        {"input": {"teamId": team_guid, "name": "Gateway 2", "slug": "gateway"}},
    )
    payload = res["data"]["createProject"]
    expect("conflict", payload["ok"] is False)
    expect("CONFLICT code", payload["errors"][0]["code"] == "CONFLICT")

    print("step 7: project denormalizes organization_id from team")
    proj = Project.all_objects.get(guid=project_guid)
    expect("project.org == team.org", proj.organization_id == org.id)

    print("step 8: updateProject")
    res = gql(UPDATE_PROJECT, {"input": {"id": project_guid, "name": "Gateway v2"}})
    payload = res["data"]["updateProject"]
    expect("ok", payload["ok"], payload.get("errors"))
    expect("name updated", payload["data"]["name"] == "Gateway v2")

    print("step 9: softDeleteProject + slug reclaimable")
    res = gql(DELETE_PROJECT, {"input": {"id": project_guid}})
    payload = res["data"]["softDeleteProject"]
    expect("ok", payload["ok"], payload.get("errors"))
    expect("hidden", Project.objects.filter(guid=project_guid).exists() is False)

    res = gql(
        CREATE_PROJECT,
        {"input": {"teamId": team_guid, "name": "Gateway again", "slug": "gateway"}},
    )
    payload = res["data"]["createProject"]
    expect("slug reclaimable", payload["ok"], payload.get("errors"))

    print("step 10: softDeleteTeam")
    res = gql(DELETE_TEAM, {"input": {"id": team_guid}})
    payload = res["data"]["softDeleteTeam"]
    expect("ok", payload["ok"], payload.get("errors"))
    expect("hidden", Team.objects.filter(guid=team_guid).exists() is False)

    # Cleanup
    Organization.all_objects.filter(slug=f"smoke-{suffix}").delete()

    print("\nall team+project smoke checks passed.")


if __name__ == "__main__":
    main()
