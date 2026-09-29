import { describe, expect, it } from "vitest";

import {
  BINDINGS,
  INVITATIONS,
  MEMBERS,
  RESOLVED_INVITATIONS,
  ROLES,
  peopleRows,
} from "./members.fixtures";
import {
  buildPeopleRows,
  PEOPLE_LIST,
  type PeopleRow,
  selectPeople,
  viewNeeds,
} from "./people-model";

const NOW = Date.parse("2026-09-28T12:00:00Z");
const ROWS = peopleRows();

function view(key: string, filters: Record<string, string> = {}, me: string | null = "ada") {
  const v = PEOPLE_LIST.views.find((x) => x.key === key)!;
  return selectPeople(ROWS, {
    filters: { ...v.filters, ...filters },
    q: "",
    sort: PEOPLE_LIST.defaultSort,
    page: 1,
    pageSize: 25,
    me,
    now: Date.now(),
  });
}

const names = (rows: PeopleRow[]) =>
  rows.map((r) =>
    r.kind === "user" ? r.user.username : r.kind === "group" ? r.externalId : r.invitation.email
  );

describe("people rows", () => {
  it("one row per user, however many member rows they hold", () => {
    const { users } = buildPeopleRows({ members: MEMBERS, bindings: BINDINGS, roles: ROLES });
    expect(users.filter((u) => u.user.username === "grace")).toHaveLength(1);
    expect(users.find((u) => u.user.username === "grace")!.memberships).toHaveLength(2);
  });

  it("names a team membership from the TEAM bindings' labels", () => {
    const { users } = buildPeopleRows({ members: MEMBERS, bindings: BINDINGS, roles: ROLES });
    expect(users.find((u) => u.user.username === "linus")!.teams).toEqual([
      { pk: "14", slug: "platform" },
    ]);
  });

  it("IdP groups come from the bindings they hold", () => {
    const { groups } = buildPeopleRows({ members: MEMBERS, bindings: BINDINGS, roles: ROLES });
    expect(groups.map((g) => g.externalId).sort()).toEqual([
      "okta:platform-admins",
      "okta:release-managers",
    ]);
  });
});

describe("views", () => {
  it("All holds users and groups, not invitations", () => {
    const all = names(view("all").rows);
    expect(all).toContain("grace");
    expect(all).toContain("okta:release-managers");
    expect(all).not.toContain("katherine@example.com");
  });

  it("Mine is the people who share a team with the viewer", () => {
    expect(names(view("mine").rows)).toEqual(["ada", "linus"]);
    expect(view("mine", {}, null).totalCount).toBe(0);
  });

  it("Invited is pending invitations; a status chip shows the history", () => {
    expect(view("invited").totalCount).toBe(INVITATIONS.length);
    expect(names(view("invited", { status: "revoked" }).rows)).toEqual(["barbara@example.com"]);
    expect(RESOLVED_INVITATIONS.length).toBeGreaterThan(0);
  });

  it("Groups is IdP groups only", () => {
    expect(view("groups").rows.every((r) => r.kind === "group")).toBe(true);
  });

  it("Admins hold, at org scope, a role that can manage members", () => {
    expect(names(view("admins").rows)).toEqual(["ada", "grace", "okta:platform-admins"]);
  });

  it("each view runs only the walks it reads", () => {
    const v = (key: string) => PEOPLE_LIST.views.find((x) => x.key === key)!.filters;
    expect(viewNeeds(v("invited"))).toEqual({ members: false, bindings: false, invitations: true });
    expect(viewNeeds(v("groups"))).toEqual({ members: false, bindings: true, invitations: false });
    expect(viewNeeds(v("all")).invitations).toBe(false);
  });
});

describe("filters, sort and pages", () => {
  it("filters by role, team, scope and last active", () => {
    expect(names(view("all", { role: "org_admin" }).rows)).toEqual([
      "grace",
      "okta:platform-admins",
    ]);
    expect(names(view("all", { team: "platform" }).rows)).toEqual(["ada", "linus"]);
    expect(names(view("all", { scope: "APP" }).rows)).toEqual(["grace"]);
    expect(names(view("all", { active: "never" }).rows)).toEqual(["margaret"]);
    expect(names(view("all", { active: "stale" }).rows)).toEqual(["linus"]);
  });

  it("sorts and numbers the pages", () => {
    const base = {
      filters: {},
      q: "",
      me: null,
      now: NOW,
      sort: [{ key: "name", dir: "desc" as const }],
    };
    const page1 = selectPeople(ROWS, { ...base, page: 1, pageSize: 2 });
    const page2 = selectPeople(ROWS, { ...base, page: 2, pageSize: 2 });
    expect(page1.totalCount).toBe(6);
    expect(names(page1.rows)).toEqual(["okta:release-managers", "okta:platform-admins"]);
    expect(names(page2.rows)).toEqual(["margaret", "linus"]);
    expect(page1.filtered).toHaveLength(6);
  });

  it("searches groups here; users were searched on the server", () => {
    const hit = selectPeople(ROWS, {
      filters: { kind: "group" },
      q: "release",
      sort: PEOPLE_LIST.defaultSort,
      page: 1,
      pageSize: 25,
      me: null,
      now: NOW,
    });
    expect(names(hit.rows)).toEqual(["okta:release-managers"]);
  });
});
