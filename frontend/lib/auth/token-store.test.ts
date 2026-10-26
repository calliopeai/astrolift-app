import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Signed out, every GraphQL operation asked /api/session/getToken first and
 * got a 401, one round trip per operation: on CONFLICT's invitation page that
 * was a 401 pair per click, one to four seconds apart.
 */
async function freshStore() {
  vi.resetModules();
  return import("./token-store");
}

const getTokenCalls = (fetch: ReturnType<typeof vi.fn>) =>
  fetch.mock.calls.filter(([url]) => String(url) === "/api/session/getToken").length;

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("the client token", () => {
  it("asks getToken once after it answers 401", async () => {
    const fetch = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    const { getClientToken } = await freshStore();

    expect(await getClientToken()).toBeNull();
    expect(await getClientToken()).toBeNull();
    expect(await getClientToken()).toBeNull();
    expect(getTokenCalls(fetch)).toBe(1);
  });

  it("shares one lookup between requests issued together", async () => {
    let answer: (response: Response) => void = () => {};
    const fetch = vi.fn(() => new Promise<Response>((resolve) => (answer = resolve)));
    vi.stubGlobal("fetch", fetch);
    const { getClientToken } = await freshStore();

    const first = getClientToken();
    const second = getClientToken();
    answer(new Response(JSON.stringify({ Authorization: "Session abc" }), { status: 200 }));

    expect(await Promise.all([first, second])).toEqual(["Session abc", "Session abc"]);
    expect(getTokenCalls(fetch)).toBe(1);
    expect(localStorage.getItem("jwt")).toBe("Session abc");
  });

  it("asks again after a failure that is not a 401", async () => {
    const fetch = vi.fn(async () => new Response("{}", { status: 500 }));
    vi.stubGlobal("fetch", fetch);
    const { getClientToken } = await freshStore();

    await getClientToken();
    await getClientToken();
    expect(getTokenCalls(fetch)).toBe(2);
  });

  it("uses a stored token without asking", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    localStorage.setItem("jwt", "Session stored");
    const { getClientToken } = await freshStore();

    expect(await getClientToken()).toBe("Session stored");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("costs one getToken per page, not one per GraphQL operation, while signed out", async () => {
    const fetch = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    vi.resetModules();
    const { buildClientLinks } = await import("@/lib/apollo/links");
    const { ApolloClient, HttpLink, InMemoryCache, gql } = await import("@apollo/client");
    const graphql = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            errors: [
              { message: "Authentication required.", extensions: { code: "UNAUTHENTICATED" } },
            ],
          }),
          { status: 401, headers: { "content-type": "application/json" } }
        )
    );
    const client = new ApolloClient({
      cache: new InMemoryCache(),
      link: buildClientLinks(new HttpLink({ uri: "http://test/app/gql/config/", fetch: graphql })),
    });
    const mutation = gql`
      mutation AcceptInvitation($input: AcceptInvitationInput!) {
        acceptInvitation(input: $input) {
          ok
        }
      }
    `;

    for (let press = 0; press < 3; press++) {
      await client.mutate({ mutation, variables: { input: { token: "x" } } }).catch(() => {});
    }

    expect(graphql).toHaveBeenCalledTimes(3);
    expect(getTokenCalls(fetch)).toBe(1);
  });

  it("prefers a token stored after a 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 401 }))
    );
    const { getClientToken } = await freshStore();

    expect(await getClientToken()).toBeNull();
    localStorage.setItem("jwt", "Session signed-in-elsewhere");
    expect(await getClientToken()).toBe("Session signed-in-elsewhere");
  });

  it("invalidates a captured session across logout and re-login even with the same token", async () => {
    const { captureClientSession, setClientToken, clearToken } = await freshStore();
    expect(captureClientSession()).toBeNull();
    setClientToken("Session original");
    const current = captureClientSession();
    expect(current?.()).toBe(true);
    clearToken();
    setClientToken("Session original");
    expect(current?.()).toBe(false);
  });

  it("notifies pending handoffs on local and cross-tab session changes, then cleans up", async () => {
    const { watchClientSessionChanges, setClientToken, clearToken } = await freshStore();
    const changed = vi.fn();
    const stop = watchClientSessionChanges(changed);
    setClientToken("Session first");
    clearToken();
    window.dispatchEvent(new StorageEvent("storage", { key: "jwt" }));
    window.dispatchEvent(new StorageEvent("storage", { key: null }));
    window.dispatchEvent(new Event("astrolift:session-expired"));
    window.dispatchEvent(new StorageEvent("storage", { key: "unrelated" }));
    expect(changed).toHaveBeenCalledTimes(5);
    stop();
    setClientToken("Session next");
    window.dispatchEvent(new StorageEvent("storage", { key: "jwt" }));
    expect(changed).toHaveBeenCalledTimes(5);
  });
});
