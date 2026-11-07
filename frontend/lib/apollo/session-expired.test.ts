import { ApolloClient, gql, HttpLink, InMemoryCache } from "@apollo/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildClientLinks } from "./links";
import { clearActiveOrgGuid, setActiveOrgGuid } from "@/lib/identity/active-org";

vi.mock("@/lib/auth/token-store", () => ({
  getClientToken: async () => null,
  clearToken: vi.fn(),
}));

const QUERY = gql`
  query Me {
    me {
      id
    }
  }
`;

function client(status: number, body: unknown) {
  const fetch = async () =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  return new ApolloClient({
    cache: new InMemoryCache(),
    link: buildClientLinks(new HttpLink({ uri: "http://test/app/gql/config/", fetch })),
  });
}

async function expired(status: number, body: unknown) {
  const seen = vi.fn();
  window.addEventListener("astrolift:session-expired", seen);
  try {
    await client(status, body)
      .query({ query: QUERY, fetchPolicy: "no-cache" })
      .catch(() => {});
  } finally {
    window.removeEventListener("astrolift:session-expired", seen);
  }
  return seen.mock.calls.length;
}

afterEach(() => vi.clearAllMocks());

describe("an expired session reaches the sign-in prompt", () => {
  it("on the 401 the API answers before GraphQL runs", async () => {
    const body = {
      errors: [{ message: "Authentication required.", extensions: { code: "UNAUTHENTICATED" } }],
    };
    expect(await expired(401, body)).toBe(1);
  });

  it("on an UNAUTHENTICATED GraphQL error", async () => {
    const body = {
      data: null,
      errors: [{ message: "x", extensions: { code: "UNAUTHENTICATED" } }],
    };
    expect(await expired(200, body)).toBe(1);
  });

  it("not on a refusal that is not about the session", async () => {
    expect(await expired(403, { errors: [{ message: "Send the X-Platform header." }] })).toBe(0);
    expect(await expired(500, {})).toBe(0);
  });
});

it("omits response bodies from sensitive network diagnostics", async () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    const api = client(500, { private: "native-kubeconfig-private-key" });
    await api
      .query({ query: QUERY, fetchPolicy: "no-cache", context: { redactDiagnostics: true } })
      .catch(() => {});
    expect(log).toHaveBeenCalledWith("[Apollo] Sensitive request failed");
    expect(JSON.stringify(log.mock.calls)).not.toContain("native-kubeconfig-private-key");
    api.stop();
  } finally {
    log.mockRestore();
  }
});

it("does not send a request after its reviewed organization has changed", async () => {
  const fetch = vi.fn(
    async () =>
      new Response(JSON.stringify({ data: { me: { id: "id" } } }), {
        headers: { "content-type": "application/json" },
      })
  );
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  const api = new ApolloClient({
    cache: new InMemoryCache(),
    link: buildClientLinks(new HttpLink({ uri: "http://test/app/gql/config/", fetch })),
  });
  setActiveOrgGuid("new-organization");
  try {
    await expect(
      api.query({
        query: QUERY,
        fetchPolicy: "no-cache",
        context: {
          expectedNvkeOrganization: "reviewed-organization",
          redactDiagnostics: true,
        },
      })
    ).rejects.toThrow("NVKE_SOURCE_CHANGED");
    expect(fetch).not.toHaveBeenCalled();
  } finally {
    clearActiveOrgGuid();
    log.mockRestore();
    api.stop();
  }
});
