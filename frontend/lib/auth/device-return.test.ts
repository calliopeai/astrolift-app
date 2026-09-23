import { describe, expect, it } from "vitest";

import { authCallbackUrl, deviceApprovalReturnPath } from "./device-return";

describe("device approval login return", () => {
  it("keeps a valid CLI approval path", () => {
    expect(deviceApprovalReturnPath("/app/cli/auth/device/abc_DEF-123/")).toBe(
      "/app/cli/auth/device/abc_DEF-123/"
    );
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
  ])("rejects an unsafe or unrelated return address: %s", (value) => {
    expect(deviceApprovalReturnPath(value)).toBe("/dashboard");
  });

  it("carries the approval path through the frontend SSO callback", () => {
    const path = "/app/cli/auth/device/abc_DEF-123/";
    const callback = new URL(authCallbackUrl("https://astro.example", path));
    expect(callback.pathname).toBe("/auth/callback");
    expect(callback.searchParams.get("next")).toBe(path);
  });
});
