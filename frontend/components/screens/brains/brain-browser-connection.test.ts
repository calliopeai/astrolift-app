import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BrainConnection } from "./brain-connection";
import { brainBrowserOrigin, openBrainBrowser } from "./brain-browser-connection";

const ID = "6f1c2a8e-0000-4000-8000-00000000b001";
const ORIGIN = "https://brain.example.test";
const NONCE = "a".repeat(64);
const TOKEN = "eyJhbGciOiJFUzI1NiJ9.eyJzY29wZSI6ImJyYWluOnJlYWQifQ.c2ln";
const ready = { kind: "astrolift-brain-ready/v1", nonce: NONCE, brainId: ID, returnPath: "/" };
const connected = { ...ready, kind: "astrolift-brain-connected/v1" };

function connection(): BrainConnection {
  return { id: ID, slug: "synthetic", header: "X-Astrolift-Brain-Token", token: TOKEN,
    endpoints: [ORIGIN], expiresAt: new Date(Date.now() + 900_000).toISOString() };
}

function browser() {
  const listeners = new Set<(event: MessageEvent<unknown>) => Promise<void>>();
  const popup = { closed: false, close: vi.fn(() => { popup.closed = true; }), postMessage: vi.fn() };
  const raw = {
    location: { origin: "https://astro.example.test" },
    addEventListener: (_name: string, fn: (event: MessageEvent<unknown>) => Promise<void>) => listeners.add(fn),
    removeEventListener: (_name: string, fn: (event: MessageEvent<unknown>) => Promise<void>) => listeners.delete(fn),
    setTimeout, clearTimeout, setInterval, clearInterval,
    open: vi.fn((_url: string, _target?: string, _features?: string) => popup),
  };
  return {
    raw, popup, listeners, view: raw as unknown as Window,
    send: async (data: unknown, origin = ORIGIN, source: unknown = popup) => {
      for (const receive of listeners) await receive({ data, origin, source } as MessageEvent<unknown>);
    },
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("explicit person browser Connect", () => {
  it("waits for selected OIDC popup before mint, binds returned endpoint and waits for acknowledgement", async () => {
    const b = browser();
    const issue = vi.fn(async () => connection());
    const pending = openBrainBrowser({ brainId: ID, slug: "synthetic", endpoint: ORIGIN, issue }, b.view);
    expect(issue).not.toHaveBeenCalled();
    expect(b.raw.open).toHaveBeenCalledWith(ORIGIN + "/__astrolift/brain-browser/login?return=%2F", "_blank", expect.any(String));
    await b.send(ready);
    expect(issue).toHaveBeenCalledTimes(1);
    expect(b.popup.postMessage).toHaveBeenCalledWith({ kind: "astrolift-brain-token/v1", nonce: NONCE, token: TOKEN }, ORIGIN);
    expect(b.raw.open.mock.calls[0][0]).not.toContain(TOKEN);
    await b.send({ ...connected, nonce: "b".repeat(64) });
    expect(b.listeners.size).toBe(1);
    await b.send(connected);
    await pending;
    expect(b.listeners.size).toBe(0);
    expect(b.popup.close).not.toHaveBeenCalled();
  });

  it("never mints for foreign origin/window/brain/path, extra fields, or unsolicited tokens", async () => {
    const b = browser();
    const issue = vi.fn(async () => connection());
    const abort = new AbortController();
    const pending = openBrainBrowser({ brainId: ID, slug: "synthetic", endpoint: ORIGIN, issue, signal: abort.signal }, b.view);
    const failure = expect(pending).rejects.toThrow("unavailable");
    await b.send(ready, "https://other.example.test");
    await b.send(ready, ORIGIN, {});
    for (const bad of [{ ...ready, brainId: "other" }, { ...ready, returnPath: "//other" },
      { ...ready, token: TOKEN }, { ...ready, nonce: "short" }, { ...ready, kind: "astrolift-brain-token/v1" }]) await b.send(bad);
    expect(issue).not.toHaveBeenCalled();
    abort.abort();
    await failure;
    expect(b.listeners.size).toBe(0);
  });

  it("refuses stale or wrong actual mint bindings without disclosing the token to popup", async () => {
    for (const change of [{ id: "other" }, { slug: "other" }, { header: "Authorization" },
      { endpoints: ["https://other.example.test"] }, { expiresAt: "invalid" },
      { expiresAt: new Date(Date.now() - 1).toISOString() }, { token: "x".repeat(3501) }]) {
      const b = browser();
      const issue = vi.fn(async () => ({ ...connection(), ...change }));
      const pending = openBrainBrowser({ brainId: ID, slug: "synthetic", endpoint: ORIGIN, issue }, b.view);
      const failure = expect(pending).rejects.toThrow("unavailable");
      await b.send(ready);
      await failure;
      expect(b.popup.postMessage).not.toHaveBeenCalled();
      expect(b.listeners.size).toBe(0);
    }
  });

  it("ignores repeated ready messages and refuses a rejected APP_READ mutation", async () => {
    const b = browser();
    const issue = vi.fn(async () => { throw new Error("PRIVATE_CANARY"); });
    const pending = openBrainBrowser({ brainId: ID, slug: "synthetic", endpoint: ORIGIN, issue }, b.view);
    const failure = expect(pending).rejects.toThrow("Brain browser connection unavailable");
    await b.send(ready);
    await failure;
    await b.send(ready);
    expect(issue).toHaveBeenCalledTimes(1);
    expect(b.popup.postMessage).not.toHaveBeenCalled();
  });

  it("bounds waiting and aborts normal pending mutation without later token delivery", async () => {
    const b = browser();
    let answer: ((value: BrainConnection) => void) | undefined;
    let signal: AbortSignal | undefined;
    const issue = vi.fn((value: AbortSignal) => { signal = value; return new Promise<BrainConnection>((resolve) => { answer = resolve; }); });
    const pending = openBrainBrowser({ brainId: ID, slug: "synthetic", endpoint: ORIGIN, issue }, b.view);
    const failure = expect(pending).rejects.toThrow("unavailable");
    const delivery = b.send(ready);
    await vi.advanceTimersByTimeAsync(120_001);
    await failure;
    expect(signal?.aborted).toBe(true);
    answer?.(connection());
    await delivery;
    expect(b.popup.postMessage).not.toHaveBeenCalled();
    expect(b.listeners.size).toBe(0);
  });

  it("handles blocked popup, user close, and thrown window-open without lingering listener", async () => {
    for (const mode of ["blocked", "closed", "throw"] as const) {
      const b = browser();
      if (mode === "blocked") b.raw.open.mockImplementation(() => null as unknown as typeof b.popup);
      if (mode === "throw") b.raw.open.mockImplementation(() => { throw new Error("blocked"); });
      const issue = vi.fn(async () => connection());
      const pending = openBrainBrowser({ brainId: ID, slug: "synthetic", endpoint: ORIGIN, issue }, b.view);
      const failure = expect(pending).rejects.toThrow("unavailable");
      if (mode === "closed") { b.popup.closed = true; await vi.advanceTimersByTimeAsync(251); }
      await failure;
      expect(issue).not.toHaveBeenCalled();
      expect(b.listeners.size).toBe(0);
    }
  });

  it("rejects nonHTTPS, credentials, nested origins, queries and fragment selectors", () => {
    for (const value of ["http://brain.example.test", "https://user:secret@brain.example.test",
      ORIGIN + "/other", ORIGIN + "?token=x", ORIGIN + "#fragment", "//brain.example.test"]) {
      expect(() => brainBrowserOrigin(value)).toThrow();
    }
  });
});
