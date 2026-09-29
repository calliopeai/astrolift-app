import { describe, expect, it } from "vitest";

import { VALID_MANIFEST } from "./apps-wizard-steps-a.fixtures";
import { deployPreview } from "./deploy-preview";

describe("deployPreview", () => {
  it("lists the manifest's workloads and classifies the app", () => {
    expect(deployPreview(VALID_MANIFEST)).toEqual({
      workloads: [{ name: "web", kind: "deployment", replicas: 2 }],
      services: [],
      topology: "service",
    });
  });

  it("reads managed services into the topology", () => {
    const raw = `${VALID_MANIFEST}\n[[managed_services]]\nkind = "postgres"\nname = "db"\n`;
    const out = deployPreview(raw);
    expect(out?.services).toEqual(["postgres"]);
    expect(out?.topology).toBe("service-data");
  });

  it("is null for an empty or unparseable manifest", () => {
    expect(deployPreview("  ")).toBeNull();
    expect(deployPreview('name = "x"\n[[workloads]\n')).toBeNull();
  });
});
