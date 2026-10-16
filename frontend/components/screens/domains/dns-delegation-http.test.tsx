import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { buildSchema, graphql } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DomainDetailClient } from "@/app/(app)/domains/[id]/domain-detail-client";
import { ELEVATED_EVENT } from "@/lib/auth/step-up-events";
import en from "@/messages/en.json";
import { DELEGATION } from "./DnsDelegationPanel.stories";
import { DnsDelegationPanel } from "./DnsDelegationPanel";
import { DNS_WITHHELD, DOMAIN_ACTIVE, DOMAIN_DIAGNOSTICS } from "./domains-environments.fixtures";
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
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
const TOKEN = "cf-write-token-1787-never-echoed";
const ZONE = { id: "0123456789abcdef0123456789abcdef", name: "acme.example" };
const base = {
  provider: "CLOUDFLARE",
  authMethod: "API_TOKEN",
  state: "ACTIVE",
  revocationState: "NOT_REQUESTED",
  verifiedAt: "2026-10-04T01:00:00Z",
  expiresAt: null,
};
type Connection = typeof base & {
  id: string;
  version: number;
  name: string;
  dnsWritesSupported: boolean;
  delegationZoneName: string;
};
type Request = { operationName: string; variables: Record<string, unknown> };
let server: Server, client: ApolloClient, domain: typeof DOMAIN_ACTIVE;
let connection: Connection, requests: Request[], errors: string[], stepUp: boolean;
let withheld: { capability: string; reason: string }[];
beforeEach(async () => {
  domain = structuredClone(DOMAIN_ACTIVE);
  connection = {
    ...base,
    id: "66666666-6666-4666-8666-666666666666",
    version: 3,
    name: "Public DNS",
    dnsWritesSupported: false,
    delegationZoneName: "",
  };
  requests = [];
  errors = [];
  stepUp = true;
  withheld = [];
  const refused = (code: string) => ({ ok: false, errors: [{ code, message: code }], data: null });
  const roots = {
    astroliftWithheldCapabilities: () => withheld,
    astroliftManagedDomain: () => domain,
    astroliftManagedDomainDiagnostics: () => ({
      ...DOMAIN_DIAGNOSTICS,
      id: domain.id,
      zone: domain.zone,
      version: domain.version,
    }),
    dnsProviderConnectionSupport: () => ({
      allowed: true,
      reason: "",
      apiTokenSupported: true,
      oauthConfigured: false,
      oauthSetupReason: "",
      dnsWritesSupported: false,
    }),
    dnsProviderDomainBinding: () => null,
    dnsProviderConnectionsPage: () => ({
      page: 1,
      pageSize: 50,
      totalCount: 1,
      nextCursor: null,
      items: [connection],
    }),
    cloudflareDnsZones: () => ({
      complete: true,
      reason: "",
      items: [
        { ...ZONE, accountId: "f".repeat(32), status: "active", nameServers: [] },
        {
          id: "a".repeat(32),
          name: "other.example",
          accountId: "f".repeat(32),
          status: "active",
          nameServers: [],
        },
      ],
    }),
    grantCloudflareDelegation: ({
      input,
    }: {
      input: { expectedVersion: number; zoneId: string };
    }) => {
      if (stepUp) {
        stepUp = false;
        return refused("STEP_UP_REQUIRED");
      }
      if (input.expectedVersion !== connection.version) return refused("VERSION_MISMATCH");
      connection = {
        ...connection,
        version: connection.version + 1,
        dnsWritesSupported: true,
        delegationZoneName: ZONE.name,
      };
      return { ok: true, errors: [], data: connection };
    },
    bindManagedDomainDelegation: ({
      input,
    }: {
      input: { expectedDomainVersion: number; expectedConnectionVersion: number };
    }) => {
      if (input.expectedDomainVersion !== domain.version) return refused("DOMAIN_CHANGED");
      if (input.expectedConnectionVersion !== connection.version)
        return refused("VERSION_MISMATCH");
      domain = {
        ...domain,
        version: domain.version + 1,
        delegationConnectionId: connection.id,
        delegationZoneName: connection.delegationZoneName,
      };
      return { ok: true, errors: [], data: connection };
    },
  };
  server = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const payload = JSON.parse(body);
    requests.push(payload);
    const result = await graphql({
      schema,
      source: payload.query,
      variableValues: payload.variables,
      rootValue: roots,
    });
    for (const error of result.errors ?? []) errors.push(error.message);
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(result));
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
  expect(errors).toEqual([]);
});
function View() {
  return (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <DomainDetailClient id={domain.id} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
}
const panel = () =>
  screen.getByRole("heading", { name: en.domainDelegation.title }).closest("section")!;
const sent = (name: string) => requests.filter((r) => r.operationName === name);

describe("parent zone delegation through the actual schema (#1787)", () => {
  it("grants once behind step-up without echoing the token, then binds the detected connection", async () => {
    render(<View />);
    await screen.findByRole("heading", { name: en.domainDelegation.title });
    expect(within(panel()).getByText(en.domainDelegation.unbound)).toBeInTheDocument();
    expect(await within(panel()).findByText(en.domainDelegation.noneDetected)).toBeInTheDocument();

    fireEvent.change(await within(panel()).findByLabelText(en.domainDelegation.connection), {
      target: { value: connection.id },
    });
    // Only the zone that is a parent of apps.acme.example is offered.
    await within(panel()).findByText(ZONE.name);
    expect(within(panel()).queryByText("other.example")).toBeNull();
    const field = within(panel()).getByLabelText(en.domainDelegation.token);
    expect(field).toHaveAttribute("type", "password");
    fireEvent.change(field, { target: { value: TOKEN } });
    fireEvent.click(within(panel()).getByRole("button", { name: en.domainDelegation.grant }));

    await waitFor(() => expect(sent("GrantCloudflareDelegation")).toHaveLength(1));
    await waitFor(() => {
      window.dispatchEvent(new Event(ELEVATED_EVENT));
      expect(sent("GrantCloudflareDelegation")).toHaveLength(2);
    });
    await within(panel()).findByText(`Write access granted for ${ZONE.name}.`);
    for (const r of sent("GrantCloudflareDelegation"))
      expect(r.variables).toEqual({
        input: {
          connectionId: "66666666-6666-4666-8666-666666666666",
          expectedVersion: 3,
          zoneId: ZONE.id,
          zoneName: ZONE.name,
          token: TOKEN,
        },
      });
    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect(await within(panel()).findByLabelText(en.domainDelegation.token)).toHaveValue("");

    const bind = await within(panel()).findByRole("button", { name: en.domainDelegation.bind });
    await waitFor(() => expect(bind).toBeEnabled());
    fireEvent.click(bind);
    await waitFor(() =>
      expect(within(panel()).getByText(`From ${ZONE.name} through Public DNS`)).toBeInTheDocument()
    );
    expect(sent("BindManagedDomainDelegation")[0].variables).toEqual({
      input: {
        domainId: DOMAIN_ACTIVE.id,
        expectedDomainVersion: 7,
        connectionId: "66666666-6666-4666-8666-666666666666",
        expectedConnectionVersion: 4,
      },
    });
    expect(
      await within(panel()).findByRole("button", { name: en.domainDelegation.bound })
    ).toBeDisabled();
  });

  it("shows the last delegation write recorded by provisioning", async () => {
    domain = {
      ...domain,
      delegationConnectionId: connection.id,
      delegationZoneName: ZONE.name,
      delegationWrite: {
        action: "conflict",
        reason: "apps.acme.example already has CNAME records",
      },
    };
    render(<View />);
    await screen.findByRole("heading", { name: en.domainDelegation.title });
    expect(within(panel()).getByText(en.domainDelegation.action.conflict)).toBeInTheDocument();
    expect(
      within(panel()).getByText("apps.acme.example already has CNAME records")
    ).toBeInTheDocument();
  });
  it("names the binding for a viewer who cannot list connections", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <DnsDelegationPanel
          {...DELEGATION}
          canWrite={false}
          connections={null}
          boundConnectionId={connection.id}
          boundZoneName={ZONE.name}
        />
      </NextIntlClientProvider>
    );
    expect(
      screen.getByText(`From ${ZONE.name} through ${en.domainDelegation.connection}`)
    ).toBeInTheDocument();
    expect(screen.getByText(en.domainDelegation.operatorOnly)).toBeInTheDocument();
  });

  it("is read-only with the reason when the install withholds DNS (calliope-installer#447)", async () => {
    withheld = [{ capability: "dns", reason: DNS_WITHHELD }];
    domain = {
      ...domain,
      delegationConnectionId: connection.id,
      delegationZoneName: ZONE.name,
      delegationWrite: { action: "created", nameservers: ["ns-1.awsdns-01.org"] },
    };
    render(<View />);
    await screen.findByRole("heading", { name: en.domainDelegation.title });
    expect(await within(panel()).findByText(en.domainDelegation.withheld)).toBeInTheDocument();
    expect(within(panel()).getByText("ns-1.awsdns-01.org")).toBeInTheDocument();
    expect(within(panel()).queryByLabelText(en.domainDelegation.token)).toBeNull();
    expect(within(panel()).queryByLabelText(en.domainDelegation.connection)).toBeNull();
    expect(within(panel()).queryByRole("button")).toBeNull();
    expect(sent("GrantCloudflareDelegation")).toHaveLength(0);
    expect(sent("BindManagedDomainDelegation")).toHaveLength(0);
  });
});
