import { describe, expect, it } from "vitest";

import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import type { AstroliftAppTeamAccess } from "@/graphql/registry/registry.types";

import { accessRows, bindingRow, shareRow, viewSources } from "./app-access-rows";

const userBinding = {
  id: "rb-1",
  user: { id: "u-1", username: "dana", email: "dana@example.com" },
  groupExternalId: "",
  role: { id: "r-1", slug: "app-deployer", name: "App deployer", scopeLevel: "APP" },
  scopeKind: "APP",
  scopeId: "app-1",
  sourceScopeLabel: "checkout",
  grantedAt: "2026-09-01T12:00:00Z",
  expiresAt: null,
  inherits: true,
} as unknown as AstroliftRoleBinding;

const groupBinding = {
  ...userBinding,
  id: "rb-2",
  user: null,
  groupExternalId: "okta:eng",
} as unknown as AstroliftRoleBinding;

const share = {
  id: "s-1",
  appId: "app-1",
  appSlug: "checkout",
  teamId: "t-1",
  teamSlug: "payments",
  teamName: "Payments",
  accessLevel: "deployer",
  isHome: false,
  createdAt: "2026-08-01T00:00:00Z",
  updatedAt: "2026-08-01T00:00:00Z",
} as AstroliftAppTeamAccess;

describe("rows", () => {
  it("names a user binding's holder, links them, and calls it direct", () => {
    const row = bindingRow(userBinding);
    expect(row.principal).toMatchObject({ kind: "user", id: "u-1", name: "dana" });
    expect(row.principal.href).toBe("/administration/members/u-1");
    expect(row.role).toEqual({ name: "App deployer", slug: "app-deployer" });
    expect(row.source).toEqual({});
  });

  it("holds a group binding via the group", () => {
    const row = bindingRow(groupBinding);
    expect(row.principal).toMatchObject({ kind: "group", id: "okta:eng" });
    expect(row.source.via).toEqual({ kind: "group", group: "okta:eng" });
  });

  it("makes a team share a direct grant held by the team, its level as the role", () => {
    const row = shareRow(share);
    expect(row.principal).toMatchObject({ kind: "team", id: "payments", name: "Payments" });
    expect(row.role.slug).toBe("deployer");
    expect(row.source).toEqual({});
  });
});

describe("views", () => {
  it("reads only the sources a view shows", () => {
    expect(viewSources("all")).toEqual({ bindings: true, shares: true });
    expect(viewSources("teams")).toEqual({ bindings: false, shares: true });
    expect(viewSources("roles")).toEqual({ bindings: true, shares: false });
    expect(viewSources("mine")).toEqual({ bindings: true, shares: false });
  });

  const input = { bindings: [userBinding, groupBinding], shares: [share], meId: "u-1" };

  it("puts team shares above the bindings on All's first page only", () => {
    expect(accessRows({ ...input, view: "all", firstPage: true }).map((r) => r.id)).toEqual([
      "share:s-1",
      "binding:rb-1",
      "binding:rb-2",
    ]);
    expect(accessRows({ ...input, view: "all", firstPage: false }).map((r) => r.id)).toEqual([
      "binding:rb-1",
      "binding:rb-2",
    ]);
  });

  it("keeps only the viewer's own bindings in Mine, and none without a viewer", () => {
    expect(accessRows({ ...input, view: "mine", firstPage: true }).map((r) => r.id)).toEqual([
      "binding:rb-1",
    ]);
    expect(accessRows({ ...input, meId: null, view: "mine", firstPage: true })).toEqual([]);
  });

  it("shows only bindings in Roles and only shares in Team shares", () => {
    expect(accessRows({ ...input, view: "roles", firstPage: true })).toHaveLength(2);
    expect(accessRows({ ...input, view: "teams", firstPage: true }).map((r) => r.kind)).toEqual([
      "team_share",
    ]);
  });
});
