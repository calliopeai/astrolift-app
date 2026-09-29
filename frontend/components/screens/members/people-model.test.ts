import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import { BINDINGS, GROUPS, MEMBERS, ROLES } from "./members.fixtures";
import {
  groupRow,
  groupsVariables,
  invitationsVariables,
  membersVariables,
  PEOPLE_LIST,
  sourceOf,
  userRows,
} from "./people-model";

/** The list state a URL gives, as the question each view's query is asked. */
function question(qs: string) {
  const state = parseListState(PEOPLE_LIST, qs);
  return {
    q: state.q,
    filters: effectiveFilters(PEOPLE_LIST, state),
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  };
}

describe("people rows", () => {
  const users = userRows(MEMBERS, BINDINGS, ROLES);

  it("one row per ORG member row, with the teams the server put on it", () => {
    expect(users.map((u) => u.user.username)).toEqual(["ada", "grace", "linus", "margaret"]);
    expect(users.find((u) => u.user.username === "linus")!.teams).toEqual([
      { id: "t-platform", slug: "platform", name: "Platform" },
    ]);
  });

  it("a person's roles are the page's bindings held by their user", () => {
    const grace = users.find((u) => u.user.username === "grace")!;
    expect(grace.bindings.map((b) => b.role.slug).sort()).toEqual(["app-operator", "org_admin"]);
  });

  it("an org-scope binding of a role that manages members makes an admin", () => {
    expect(users.filter((u) => u.admin).map((u) => u.user.username)).toEqual(["ada", "grace"]);
  });

  it("an IdP group row carries its members, grants and mappings", () => {
    expect(groupRow(GROUPS[1])).toEqual({
      kind: "group",
      key: "group:okta:release-managers",
      externalId: "okta:release-managers",
      memberCount: 12,
      bindingsCount: 1,
      mappingsCount: 2,
    });
  });
});

describe("each view asks one query", () => {
  it("routes All, Mine and Admins to members, Groups and Invited to their own", () => {
    const view = (key: string) => PEOPLE_LIST.views.find((v) => v.key === key)!.filters;
    expect(sourceOf(view("all"))).toBe("members");
    expect(sourceOf(view("mine"))).toBe("members");
    expect(sourceOf(view("admins"))).toBe("members");
    expect(sourceOf(view("groups"))).toBe("groups");
    expect(sourceOf(view("invited"))).toBe("invitations");
  });

  it("asks members for one ORG row per person on a cold load", () => {
    expect(membersVariables(question(""))).toEqual({
      search: null,
      filter: { scopeKind: ["ORG"] },
      sort: "name",
      page: 1,
      pageSize: 25,
    });
  });

  it("sends Mine, Admins and the chips as the members filter", () => {
    expect(membersVariables(question("view=mine")).filter).toEqual({
      scopeKind: ["ORG"],
      mine: true,
    });
    expect(membersVariables(question("view=admins")).filter).toEqual({
      scopeKind: ["ORG"],
      admin: true,
    });
    expect(
      membersVariables(
        question("role=org_admin&team=platform&lifecycle=suspended&active=stale&q=ada")
      )
    ).toMatchObject({
      search: "ada",
      filter: {
        scopeKind: ["ORG"],
        role: ["org_admin"],
        team: ["platform"],
        lifecycle: ["suspended"],
        active: "stale",
      },
    });
  });

  it("sends the column sorts and the page", () => {
    expect(membersVariables(question("sort=-lastActive,name&page=3&pageSize=50"))).toMatchObject({
      sort: "-lastActive,name",
      page: 3,
      pageSize: 50,
    });
  });

  it("asks invitations for the status, spelling name and joined as email and created", () => {
    expect(invitationsVariables(question("view=invited"))).toEqual({
      search: null,
      filter: { status: ["pending"] },
      sort: "email",
      page: 1,
      pageSize: 25,
    });
    expect(
      invitationsVariables(question("view=invited&status=revoked&role=viewer&sort=-joined"))
    ).toMatchObject({ filter: { status: ["revoked"], role: ["viewer"] }, sort: "-created" });
    // A sort invitations cannot take falls back to newest first, not an error.
    expect(invitationsVariables(question("view=invited&sort=-lastActive")).sort).toBe("-created");
  });

  it("asks the principal search for IdP groups only, searched and paged", () => {
    expect(groupsVariables(question("view=groups&q=release&page=2"))).toEqual({
      search: "release",
      filter: { kind: ["GROUP"] },
      page: 2,
      pageSize: 25,
    });
  });
});
