import { describe, expect, it } from "vitest";

import type { AstroliftNavTree } from "@/graphql/identity/identity.types";

import { projectsFromNavTree } from "./projects-from-nav-tree";

const app = (id: string, kind: string, status = "ready") =>
  ({ id, name: id, slug: id, primitiveKind: kind, primitiveSlug: id, status }) as never;

const TREE = {
  organization: { id: "o", slug: "conflict", name: "CONFLICT" },
  unassignedApps: [],
  teams: [
    {
      team: { id: "t", slug: "commerce", name: "commerce" },
      unassignedApps: [],
      projects: [
        {
          project: { id: "p", slug: "storefront", name: "storefront" },
          apps: [app("checkout", "app"), app("billing-api", "app", "failed")],
          standaloneAgents: [app("support-bot", "agent", "provisioning")],
          workflows: [
            { id: "w", slug: "nightly-sync", name: "nightly-sync", isEnabled: true, agents: [] },
          ],
        },
      ],
    },
  ],
} as unknown as AstroliftNavTree;

describe("projectsFromNavTree", () => {
  it("maps each project's apps, agents and workflows, with links and status tones", () => {
    const [p] = projectsFromNavTree(TREE, () => true);
    expect(p!.slug).toBe("storefront");
    expect(p!.entities.map((e) => [e.kind, e.href, e.status])).toEqual([
      ["app", "/apps/checkout", "ok"],
      ["app", "/apps/billing-api", "error"],
      ["agent", "/agents/support-bot", "pending"],
      ["workflow", "/workflows/nightly-sync", "ok"],
    ]);
  });

  it("drops what the viewer's modules do not show", () => {
    const [p] = projectsFromNavTree(TREE, (m) => m === "apps");
    expect(p!.entities.map((e) => e.kind)).toEqual(["app", "app"]);
  });

  it("is empty with no tree", () => {
    expect(projectsFromNavTree(null, () => true)).toEqual([]);
  });
});
