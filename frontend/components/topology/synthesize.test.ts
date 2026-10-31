import { describe, expect, it } from "vitest";

import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
  ReachabilityStatus,
} from "@/graphql/registry/registry.types";

import { appTopology } from "./synthesize";

const web = {
  id: "web",
  slug: "web",
  name: "web",
  kind: "deployment",
  isPublic: true,
  replicas: 1,
};

function topology(app: Partial<AstroliftRegisteredApp>) {
  return appTopology(
    { slug: "checkout", name: "checkout", subdomain: "checkout", ...app } as AstroliftRegisteredApp,
    [web as AstroliftWorkload]
  );
}

function ingress(app: Partial<AstroliftRegisteredApp>) {
  return topology(app).nodes.find((n) => n.id === "ingress");
}

describe("the public ingress node", () => {
  it.each([
    ["REACHABLE", "running"],
    ["PROVISIONING", "provisioning"],
    ["NEEDS_REPROVISION", "failed"],
  ] as [ReachabilityStatus, string][])(
    "of a ready app that is %s draws %s (#2573)",
    (reachabilityStatus, status) => {
      expect(ingress({ provisioningStatus: "ready", reachabilityStatus })?.status).toBe(status);
    }
  );

  it("is not drawn for an app with no managed domain, nor is the edge to it (#2572)", () => {
    for (const provisioningStatus of ["ready", "provisioning"] as const) {
      const { nodes, edges } = topology({
        provisioningStatus,
        reachabilityStatus: "NO_MANAGED_DOMAIN",
      });
      expect(nodes.find((n) => n.type === "ingress")).toBeUndefined();
      expect(edges.filter((e) => e.source === "ingress")).toEqual([]);
      expect(nodes.map((n) => n.id)).toEqual(["svc-web", "wl-web"]);
    }
  });

  it("follows provisioning before the app is ready", () => {
    expect(
      ingress({ provisioningStatus: "failed", reachabilityStatus: "NEEDS_REPROVISION" })?.status
    ).toBe("failed");
    expect(
      ingress({ provisioningStatus: "provisioning", reachabilityStatus: "REACHABLE" })?.status
    ).toBe("provisioning");
  });
});
