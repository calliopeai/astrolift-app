import { describe, expect, it } from "vitest";

import { authCallbackUrl, invitationPath, signInReturnPath } from "./sign-in-return";

const TOKEN = "alft_example-invitation_Token09";

describe("sign-in return address", () => {
  it("keeps a valid CLI approval path", () => {
    expect(signInReturnPath("/app/cli/auth/device/abc_DEF-123/")).toBe(
      "/app/cli/auth/device/abc_DEF-123/"
    );
  });

  it("keeps the invitation the invitee came from", () => {
    expect(signInReturnPath(invitationPath(TOKEN))).toBe(`/auth/invitation/${TOKEN}`);
  });

  it.each([
    "//evil.example/app/cli/auth/device/abc/",
    "https://evil.example/app/cli/auth/device/abc/",
    "https://astrolift.invalid/app/cli/auth/device/abc/",
    "/app/cli/auth/device/abc\\evil/",
    "/app/cli/auth/device/../../dashboard/",
    "/app/cli/auth/device/abc/?token=secret",
    "/app/cli/auth/device/abc/#fragment",
    "/app/cli/auth/device/abc",
    "//evil.example/auth/invitation/abc",
    "https://evil.example/auth/invitation/abc",
    "/\\evil.example/auth/invitation/abc",
    "/\t/evil.example/auth/invitation/abc",
    "\t/auth/invitation/abc",
    "/auth/invitation/abc/",
    "/auth/invitation/abc?exp=1790000000",
    "/auth/invitation/abc#x",
    "/auth/invitation/abc%2F..%2F..%2Fdashboard",
    "/auth/invitation/../dashboard",
    "/auth/invitation/",
    "/auth/invitations/abc",
    "/dashboard/../auth/invitation",
    "javascript:alert(1)",
    "",
    null,
  ])("rejects an unsafe or unrelated return address: %s", (value) => {
    expect(signInReturnPath(value)).toBe("/dashboard");
  });

  it("carries the return path through the frontend SSO callback", () => {
    for (const path of ["/app/cli/auth/device/abc_DEF-123/", invitationPath(TOKEN)]) {
      const callback = new URL(authCallbackUrl("https://astro.example", path));
      expect(callback.origin).toBe("https://astro.example");
      expect(callback.pathname).toBe("/auth/callback");
      expect(signInReturnPath(callback.searchParams.get("next"))).toBe(path);
    }
  });

  it("leaves the dashboard implicit", () => {
    expect(authCallbackUrl("https://astro.example", "/dashboard")).toBe(
      "https://astro.example/auth/callback"
    );
  });
});
