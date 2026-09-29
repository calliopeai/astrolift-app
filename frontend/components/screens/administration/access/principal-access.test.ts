import { describe, expect, it } from "vitest";

import type { AstroliftRole, AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import {
  ACCESS_LIST,
  buildAccessRows,
  parseScopeLabel,
  scopeOfBinding,
  selectAccess,
  summarizeAccess,
  teamSlugIndex,
} from "./principal-access";
import { parsePrincipalParam, personTabs } from "./principal-tabs";

const role = (id: string, slug: string, permissions: string[] = []): AstroliftRole => ({
  id,
  slug,
  name: slug,
  description: "",
  scopeLevel: "ORG",
  permissions,
  isSystem: true,
});

const binding = (
  id: string,
  r: AstroliftRole,
  scopeKind: AstroliftRoleBinding["scopeKind"],
  label: string,
  scopeId = id
): AstroliftRoleBinding => ({
  id,
  role: r,
  scopeKind,
  scopeId,
  sourceScopeLabel: label,
  user: { id: "7", username: "ada", email: "ada@example.com", isActive: true },
  groupExternalId: "",
  grantedAt: "2026-09-01T00:00:00Z",
  expiresAt: null,
  inherits: false,
});

const VIEWER = role("r-view", "org_viewer", ["app.read"]);
const DEV = role("r-dev", "team_developer", ["app.read", "app.deploy"]);
const APP = role("r-app", "app_admin", ["app.update"]);

const BINDINGS = [
  binding("1", APP, "APP", "app checkout-api"),
  binding("2", DEV, "TEAM", "team platform", "14"),
  binding("3", VIEWER, "ORG", "organization acme"),
  binding("4", DEV, "PROJECT", "project platform/checkout"),
  binding("5", APP, "APP", "app checkout-worker"),
];

const ROWS = buildAccessRows(BINDINGS, [VIEWER, DEV, APP]);
const select = (filters: Record<string, string> = {}, q = "") =>
  selectAccess(ROWS, { filters, q, sort: ACCESS_LIST.defaultSort, page: 1, pageSize: 25 });

describe("scope labels", () => {
  it("reads the server's labels", () => {
    expect(parseScopeLabel("team payments")).toEqual({ kind: "TEAM", slug: "payments" });
    expect(parseScopeLabel("project payments/checkout")).toEqual({
      kind: "PROJECT",
      slug: "payments/checkout",
    });
    expect(parseScopeLabel("organization")).toBeNull();
  });

  it("names and links a scope, or falls back to its pk", () => {
    expect(scopeOfBinding(BINDINGS[1]!)).toMatchObject({
      name: "platform",
      href: "/administration/access/teams/platform",
    });
    const unnamed = scopeOfBinding({ ...BINDINGS[1]!, sourceScopeLabel: "" });
    expect(unnamed.name).toBe("#14");
    expect(unnamed.href).toBeUndefined();
  });

  it("indexes team pks to slugs from TEAM bindings", () => {
    expect(teamSlugIndex(BINDINGS)).toEqual(new Map([["14", "platform"]]));
  });
});

describe("a principal's access", () => {
  it("lists widest first, in the resolver's order", () => {
    expect(select().rows.map((r) => r.scope.kind)).toEqual([
      "ORG",
      "TEAM",
      "PROJECT",
      "APP",
      "APP",
    ]);
  });

  it("marks a grant a wider grant of the same role already gives", () => {
    const project = ROWS.find((r) => r.id === "4")!;
    expect(project.coveredBy).toMatchObject({ kind: "TEAM", name: "platform" });
    expect(ROWS.find((r) => r.id === "2")!.coveredBy).toBeNull();
  });

  it("an org grant covers an app grant of the same role", () => {
    const rows = buildAccessRows(
      [...BINDINGS, binding("6", APP, "ORG", "organization acme")],
      [APP]
    );
    expect(rows.find((r) => r.id === "1")!.coveredBy).toMatchObject({ kind: "ORG" });
  });

  it("filters by scope, by role, and by search", () => {
    expect(select({ scope: "APP" }).totalCount).toBe(2);
    expect(select({ role: "team_developer" }).totalCount).toBe(2);
    expect(select({}, "checkout-worker").rows.map((r) => r.id)).toEqual(["5"]);
  });

  it("can:<permission> keeps the grants that carry it, and app.* any app verb", () => {
    expect(select({ can: "app.deploy" }).rows.map((r) => r.id)).toEqual(["2", "4"]);
    expect(select({ can: "app.*" }).totalCount).toBe(5);
  });

  it("summarises over every grant, not the filtered page", () => {
    const summary = summarizeAccess(ROWS);
    expect(summary).toContain("3 roles across 5 grants");
    expect(summary).toContain("2 at apps");
    expect(summary).toContain("1 also granted wider");
    expect(summarizeAccess([])).toBeNull();
  });
});

describe("principal routes", () => {
  it("tells a group param from a member id", () => {
    expect(parsePrincipalParam("group%3Aokta%3Aeng")).toEqual({
      kind: "group",
      externalId: "okta:eng",
    });
    expect(parsePrincipalParam("9c2d41e0")).toEqual({ kind: "user", memberId: "9c2d41e0" });
  });

  it("each tab is its own route", () => {
    expect(personTabs("m1", "teams").map((t) => [t.href, t.active])).toEqual([
      ["/administration/access/people/m1", false],
      ["/administration/access/people/m1/teams", true],
      ["/administration/access/people/m1/activity", false],
    ]);
  });
});
