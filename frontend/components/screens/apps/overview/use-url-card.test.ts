import { describe, expect, it } from "vitest";

import { unreachableDetail } from "./use-url-card";

const env = (
  name: string,
  reachabilityStatus: string,
  reachabilityDetail: string,
  kind = "other"
) => ({
  id: name,
  name,
  kind,
  reachabilityStatus: reachabilityStatus as Parameters<typeof unreachableDetail>[1],
  reachabilityDetail,
});

describe("unreachableDetail (#2573)", () => {
  it("says nothing for a reachable app", () => {
    expect(
      unreachableDetail([env("production", "NEEDS_REPROVISION", "No Ingress.")], "REACHABLE")
    ).toBeNull();
  });

  it("names the environment that set the app's status, not a preview sorted before it", () => {
    const environments = [
      env("preview-pr-12", "NEEDS_REPROVISION", "No hostname claim.", "preview"),
      env("production", "NEEDS_REPROVISION", "No Ingress for hello.example.", "production"),
    ];
    expect(unreachableDetail(environments, "NEEDS_REPROVISION")).toBe(
      "production: No Ingress for hello.example."
    );
  });

  it("prefers the environment whose status is the app's", () => {
    const environments = [
      env("edge", "PROVISIONING", "Certificate is being issued."),
      env("staging", "NO_MANAGED_DOMAIN", "No managed domain resolves for the organization."),
    ];
    expect(unreachableDetail(environments, "NO_MANAGED_DOMAIN")).toBe(
      "staging: No managed domain resolves for the organization."
    );
  });

  it("falls back to any unsettled environment, and to nothing without a detail", () => {
    expect(
      unreachableDetail(
        [env("staging", "PROVISIONING", "Reprovision started.")],
        "NEEDS_REPROVISION"
      )
    ).toBe("staging: Reprovision started.");
    expect(
      unreachableDetail([env("production", "NEEDS_REPROVISION", "")], "NEEDS_REPROVISION")
    ).toBeNull();
  });
});
