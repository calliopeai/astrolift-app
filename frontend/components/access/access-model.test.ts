import { describe, expect, it } from "vitest";

import {
  buildMatrix,
  canBindAt,
  describeSource,
  diffPermissions,
  scopePath,
  summarizePermissions,
} from "./access-model";
import { CHECKOUT, DEPLOYER, SCOPE_TREE, TEAM_DEV } from "./fixtures";
import { navTreeToScopes } from "./use-scope-tree";

describe("access model", () => {
  it("says where a grant comes from", () => {
    expect(describeSource({})).toBe("direct");
    expect(
      describeSource({
        via: { kind: "group", group: "okta:eng" },
        inheritedFrom: { kind: "ORG", id: "1", name: "acme" },
      })
    ).toBe("via group okta:eng, inherited from org acme");
  });

  it("binds a role at its level or narrower, with the reason otherwise", () => {
    expect(canBindAt(TEAM_DEV, "APP")).toBe(true);
    expect(canBindAt(TEAM_DEV, "TEAM")).toBe(true);
    expect(canBindAt(DEPLOYER, "PROJECT")).toMatch(/app role/);
  });

  it("finds the path to a scope", () => {
    expect(scopePath(SCOPE_TREE, CHECKOUT.kind, CHECKOUT.id)?.map((n) => n.name)).toEqual([
      "Acme",
      "Payments",
      "Storefront",
      "checkout",
    ]);
    expect(scopePath(SCOPE_TREE, "APP", "missing")).toBeNull();
  });

  it("lays the catalog out as areas × verbs, every slug exactly once", () => {
    const catalog = ["app.read", "app.deploy", "app.delete", "secret.write", "widget.read"];
    const areas = buildMatrix(catalog);
    expect(areas.map((a) => a.key)).toEqual(["apps", "other"]);
    const app = areas[0].rows.find((r) => r.resource === "app")!;
    expect(app.cells).toEqual({ read: "app.read", delete: "app.delete" });
    expect(app.more).toEqual(["app.deploy"]);
    const all = areas.flatMap((a) => a.rows.flatMap((r) => r.all));
    expect(all.sort()).toEqual([...catalog].sort());
  });

  it("diffs a role against the one it came from", () => {
    expect(diffPermissions(["a.read", "a.deploy"], ["a.read", "a.delete"])).toEqual({
      added: ["a.deploy"],
      removed: ["a.delete"],
    });
  });

  it("summarizes permissions in words", () => {
    expect(summarizePermissions([], ["app.read"])).toBe("No permissions");
    expect(summarizePermissions(["app.read"], ["app.read"])).toBe("Everything in the catalog");
    expect(summarizePermissions(["app.read", "agent.read"], ["x.y"])).toBe(
      "Read only: apps, agents"
    );
    expect(summarizePermissions(["app.read", "app.deploy"], ["x.y"])).toBe("apps: read, deploy");
  });

  it("turns the nav tree into scopes, apps outside a project under their team", () => {
    const roots = navTreeToScopes({
      organization: { id: "o", name: "Acme", slug: "acme" },
      teams: [
        {
          team: { id: "t", name: "Payments", slug: "payments" },
          projects: [
            {
              project: { id: "p", name: "Storefront", slug: "storefront" },
              apps: [{ id: "a1", name: "checkout", slug: "checkout" }],
              workflows: [{ agents: [{ id: "a2", name: "bot", slug: "bot" }] }],
              standaloneAgents: [{ id: "a1", name: "checkout", slug: "checkout" }],
            },
          ],
          unassignedApps: [{ id: "a3", name: "cron", slug: "cron" }],
        },
      ],
      unassignedApps: [],
    } as unknown as Parameters<typeof navTreeToScopes>[0]);
    const team = roots[0].children![0];
    expect(team.children!.map((n) => `${n.kind}:${n.id}`)).toEqual(["PROJECT:p", "APP:a3"]);
    expect(team.children![0].children!.map((n) => n.id)).toEqual(["a1", "a2"]);
  });
});
