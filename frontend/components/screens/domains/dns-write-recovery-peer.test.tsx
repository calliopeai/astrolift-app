import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { buildSchema, graphql } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { DNS_SETUP } from "./DnsConnectionWizard.stories";
import { DOMAIN_ACTIVE } from "./domains-environments.fixtures";
import { useDnsConnectionSetup } from "./use-dns-connection-setup";
import { useManagedDomains } from "./use-managed-domains";
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({
    org: { id: "a0000000-0000-4000-8000-000000000002", slug: "acme" },
    loading: false,
    error: null,
  }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({
    user: { id: "a0000000-0000-4000-8000-000000000003" },
    loading: false,
    error: null,
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/domains",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
let client: ApolloClient,
  server: Server,
  lost: boolean,
  failed: boolean,
  connects: number,
  creates: number;
let connections: (typeof DNS_SETUP.connection)[];
beforeEach(async () => {
  lost = failed = false;
  connects = creates = 0;
  connections = [];
  const roots = {
    dnsProviderConnectionSupport: () => DNS_SETUP.support,
    cloudflareDnsZones: () => DNS_SETUP.zones,
    dnsProviderConnectionsPage: ({ page, pageSize }: { page: number; pageSize: number }) => ({
      ...DNS_SETUP.connections,
      page,
      pageSize,
      items: connections,
      totalCount: connections.length,
    }),
    connectCloudflareDnsToken: () => {
      connects++;
      connections.push(DNS_SETUP.connection);
      return { ok: true, errors: [], data: DNS_SETUP.connection };
    },
    astroliftManagedDomainActions: () => ({
      canCreate: true,
      canDelete: false,
      canRevalidate: false,
      reason: null,
    }),
    astroliftManagedDomains: () => {
      if (failed) throw new Error("Inventory unavailable");
      return [];
    },
    createManagedDomain: () => {
      creates++;
      failed = true;
      return { ok: true, errors: [], data: DOMAIN_ACTIVE };
    },
  };
  server = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const p = JSON.parse(body);
    const r = await graphql({
      schema,
      source: p.query,
      variableValues: p.variables,
      rootValue: roots,
    });
    if (lost && p.operationName === "ConnectCloudflareToken") {
      res.destroy();
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(r));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
      fetch,
    }),
  });
});
afterEach(async () => {
  cleanup();
  client.stop();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});
function Wrapper({ children }: { children: ReactNode }) {
  return (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        {children}
      </NextIntlClientProvider>
    </ApolloProvider>
  );
}
it("blocks blind creation after accepted token reply is lost and permits explicit current selection after recovery", async () => {
  const h = renderHook(() => useDnsConnectionSetup(), { wrapper: Wrapper });
  await waitFor(() => expect(h.result.current.allowed).toBe(true));
  act(() => h.result.current.onProvider("cloudflare"));
  await waitFor(() => expect(h.result.current.connections?.items).toEqual([]));
  lost = true;
  await act(async () => {
    await h.result.current.onConnect("DNS", "synthetic-token");
  });
  await act(async () => {
    await h.result.current.onConnect("DNS", "synthetic-token");
  });
  expect(connects, "UNCERTAIN_CREATION_WAS_REPEATED").toBe(1);
  expect(h.result.current.uncertain).toBe(true);
  act(() => h.result.current.onRefresh());
  await waitFor(() => expect(h.result.current.connections?.items).toHaveLength(1));
  expect(h.result.current.uncertain).toBe(true);
  act(() => h.result.current.onConnection(h.result.current.connections!.items[0]));
  expect(h.result.current.uncertain).toBe(false);
  expect(h.result.current.connection?.id).toBe(DNS_SETUP.connection!.id);
});
it("keeps accepted Route53 creation after inventory refresh fails", async () => {
  const h = renderHook(() => useManagedDomains(), { wrapper: Wrapper });
  await waitFor(() => expect(h.result.current.canCreate).toBe(true));
  let accepted = false;
  await act(async () => {
    try {
      accepted = await h.result.current.onCreate({
        zone: DOMAIN_ACTIVE.zone,
        dnsDriver: "route53",
        defaultFor: "none",
        wildcard: false,
      });
    } catch {
      accepted = false;
    }
  });
  expect(creates).toBe(1);
  expect(accepted, "ACCEPTED_WRITE_LOST_TO_FAILED_REFRESH").toBe(true);
});
