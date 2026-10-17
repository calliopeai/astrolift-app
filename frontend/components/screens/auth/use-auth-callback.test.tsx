import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthCallback } from "./use-auth-callback";

/**
 * The backend's callback put the session key on this page's URL as
 * `?token=Session <key>`, so the load balancer, the nginx sidecar and the
 * browser's history all kept it (#2429). It now sends a one-time code, which
 * this page trades for the token with a POST that carries the session cookie
 * the code is bound to.
 */
const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  locationReplace: vi.fn(),
  params: new URLSearchParams(),
}));
const router = { replace: mocks.replace };
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => mocks.params,
}));

const TOKEN = "Session 2f0c9a6e1d7b4c38a5e4b1f09d3c7a12";
let codes = 0;
/** A code no earlier test used: the page trades each code once. */
const freshCode = () => `code-${++codes}-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_`;

function landOn(query: Record<string, string>) {
  mocks.params = new URLSearchParams(query);
}

function backendAnswers(handoff: Response) {
  const fetch = vi.fn(async (url: string, _init?: RequestInit) =>
    url.endsWith("/app/auth1/handoff") ? handoff : new Response('{"ok":true}', { status: 200 })
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const calls = (fetch: ReturnType<typeof backendAnswers>): [string, RequestInit][] =>
  fetch.mock.calls.map(([url, init]) => [url, init ?? {}]);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.stubGlobal("location", { ...window.location, replace: mocks.locationReplace });
});
afterEach(() => vi.unstubAllGlobals());

describe("the sign-in callback page", () => {
  it("trades the code for the token, stores it as before and goes to the return path", async () => {
    const code = freshCode();
    landOn({ code, next: "/auth/invitation/alft_abc" });
    const fetch = backendAnswers(
      new Response(JSON.stringify({ Authorization: TOKEN }), { status: 200 })
    );

    renderHook(() => useAuthCallback());

    await waitFor(() =>
      expect(mocks.locationReplace).toHaveBeenCalledWith("/auth/invitation/alft_abc")
    );
    const [[handoffUrl, handoff], [storeUrl, store]] = calls(fetch);
    expect(handoffUrl).toBe("/app/auth1/handoff");
    expect(handoff.method).toBe("POST");
    expect(handoff.credentials).toBe("include");
    expect(handoff.headers).toMatchObject({ "X-Platform": "web" });
    expect(handoff.headers).not.toHaveProperty("Authorization");
    expect(JSON.parse(String(handoff.body))).toEqual({ code });
    expect(storeUrl).toBe("/api/auth/store-token");
    expect(JSON.parse(String(store.body))).toEqual({ token: TOKEN });
    expect(localStorage.getItem("jwt")).toBe(TOKEN);
  });

  it("goes to the dashboard through the router when there is no return path", async () => {
    landOn({ code: freshCode() });
    backendAnswers(new Response(JSON.stringify({ Authorization: TOKEN }), { status: 200 }));

    renderHook(() => useAuthCallback());

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/dashboard"));
    expect(localStorage.getItem("jwt")).toBe(TOKEN);
  });

  it("trades a code once when React runs the effect twice", async () => {
    landOn({ code: freshCode() });
    const fetch = backendAnswers(
      new Response(JSON.stringify({ Authorization: TOKEN }), { status: 200 })
    );

    renderHook(() => useAuthCallback(), { reactStrictMode: true });

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/dashboard"));
    expect(calls(fetch).filter(([url]) => url.endsWith("/app/auth1/handoff"))).toHaveLength(1);
    expect(mocks.replace).toHaveBeenCalledTimes(1);
  });

  it("goes back to sign-in, keeping the return path, when the backend refuses the code", async () => {
    landOn({ code: freshCode(), next: "/auth/invitation/alft_abc" });
    backendAnswers(new Response('{"code":"HANDOFF_REFUSED"}', { status: 400 }));

    renderHook(() => useAuthCallback());

    await waitFor(() =>
      expect(mocks.replace).toHaveBeenCalledWith("/auth/login?next=%2Fauth%2Finvitation%2Falft_abc")
    );
    expect(localStorage.getItem("jwt")).toBeNull();
  });

  it("no longer accepts a session token on its URL", async () => {
    landOn({ token: TOKEN });
    const fetch = backendAnswers(new Response("{}", { status: 200 }));

    renderHook(() => useAuthCallback());

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/auth/login"));
    expect(fetch).not.toHaveBeenCalled();
    expect(localStorage.getItem("jwt")).toBeNull();
  });

  it.each([
    ["no_account", "/auth/login?error=no_account"],
    ["email_unverified", "/auth/login?error=email_unverified"],
    ["anything_else", "/auth/login"],
  ])("passes error=%s on to the sign-in page", async (error, login) => {
    landOn({ error });
    const fetch = backendAnswers(new Response("{}", { status: 200 }));

    renderHook(() => useAuthCallback());

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith(login));
    expect(fetch).not.toHaveBeenCalled();
  });
});
