import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IDP_EXTERNAL } from "../shell/root-bare-login.fixtures";

import { useLogin } from "./use-login";

/**
 * The callback sends the browser back to sign-in with `error=` when the IdP's
 * identity could not sign in: no account for it, or an address the IdP has
 * not verified (#2430). The page says which.
 */
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(IDP_EXTERNAL), { status: 200 }))
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("the sign-in page's notice", () => {
  it.each([
    ["no_account", "noAccount"],
    ["email_unverified", "emailUnverified"],
    ["constructor", null],
    ["something_else", null],
  ])("error=%s shows %s", async (error, notice) => {
    window.history.replaceState(null, "", `/auth/login?error=${error}`);

    const { result } = renderHook(() => useLogin());

    await waitFor(() => expect(result.current.idp).toEqual(IDP_EXTERNAL));
    expect(result.current.notice).toBe(notice);
  });

  it("shows none without an error", async () => {
    window.history.replaceState(null, "", "/auth/login");

    const { result } = renderHook(() => useLogin());

    await waitFor(() => expect(result.current.idp).toEqual(IDP_EXTERNAL));
    expect(result.current.notice).toBeNull();
  });
});
