import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { buildSchema, graphql } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DnsConnectionClient } from "@/app/(app)/domains/connect/dns-connection-client";
import en from "@/messages/en.json";
import { DNS_SETUP } from "./DnsConnectionWizard.stories";
import { DOMAIN_ACTIVE } from "./domains-environments.fixtures";

const identity = vi.hoisted(() => ({ org: "", actor: "" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org, slug: "acme" }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
// A task-local override permits proof against the real independently exported source
// while the combined production schema is being assembled. No synthesized fields.
const schema = buildSchema(
  readFileSync(
    process.env.ASTROLIFT_DNS_CONNECTION_TEST_SCHEMA ?? `${process.cwd()}/schema.graphql`,
    "utf8"
  )
);
const c = en.domainConnections;
type Request = { operationName: string; variables: Record<string, unknown> };
let view: ReturnType<typeof render>;
let server: Server,
  client: ApolloClient,
  requests: Request[],
  errors: string[],
  support: NonNullable<typeof DNS_SETUP.support>,
  connection: NonNullable<typeof DNS_SETUP.connection>,
  domain: typeof DOMAIN_ACTIVE;
let loseRegistration: boolean;
let incompleteZones: boolean,
  incompleteRecords: boolean,
  refusedConnections: boolean,
  malformed: boolean,
  explicitRefusal: boolean,
  canVerify: boolean,
  hold: string | null,
  release: (() => void) | null;
beforeEach(async () => {
  identity.org = "a0000000-0000-4000-8000-000000000002";
  identity.actor = "a0000000-0000-4000-8000-000000000003";
  support = structuredClone(DNS_SETUP.support!);
  connection = structuredClone(DNS_SETUP.connection!);
  domain = {
    ...structuredClone(DOMAIN_ACTIVE),
    zone: DNS_SETUP.selectedZone!.name,
    dnsDriver: "cloudflare_read_only",
    verificationState: "pending",
  };
  loseRegistration = false;
  incompleteZones = incompleteRecords = refusedConnections = malformed = explicitRefusal = false;
  canVerify = true;
  hold = null;
  release = null;
  requests = [];
  errors = [];
  const roots = {
    dnsProviderConnectionSupport: () => support,
    dnsProviderConnectionsPage: ({ page, pageSize }: { page: number; pageSize: number }) => {
      if (refusedConnections) throw new Error("Current connection read refused");
      return { ...DNS_SETUP.connections!, page, pageSize, items: [connection] };
    },
    cloudflareDnsZones: () => ({ ...DNS_SETUP.zones!, complete: !incompleteZones }),
    cloudflareDnsRecords: () => ({ ...DNS_SETUP.records!, complete: !incompleteRecords }),
    connectCloudflareDnsToken: () => ({ ok: true, errors: [], data: connection }),
    retestDnsProviderConnection: () => ({
      ok: true,
      errors: [],
      data: { ...connection, version: ++connection.version },
    }),
    disconnectDnsProviderConnection: () => ({
      ok: true,
      errors: [],
      data: { ...connection, state: "DISCONNECTED", version: ++connection.version },
    }),
    registerCloudflareDnsZone: () =>
      explicitRefusal
        ? {
            ok: false,
            errors: [{ code: "FORBIDDEN", message: "Current registration refused" }],
            data: null,
          }
        : {
            ok: true,
            errors: [],
            data: {
              domainId: malformed ? "d0000000-0000-4000-8000-000000000099" : domain.id,
              domainVersion: domain.version,
              connectionId: connection.id,
              connectionVersion: malformed ? connection.version + 1 : connection.version,
              zone: DNS_SETUP.selectedZone!,
              verificationState: "pending",
              verificationRecordName: domain.challengeRecordName,
              verificationRecordValue: domain.challengeRecordValue,
              dnsWritesSupported: false,
            },
          },
    attachCloudflareDnsZone: ({ input }: { input: { domainId: string } }) => ({
      ok: true,
      errors: [],
      data: {
        domainId: input.domainId,
        domainVersion: domain.version + 1,
        connectionId: connection.id,
        connectionVersion: connection.version,
        zone: DNS_SETUP.selectedZone!,
        verificationState: "pending",
        verificationRecordName: domain.challengeRecordName,
        verificationRecordValue: domain.challengeRecordValue,
        dnsWritesSupported: false,
      },
    }),
    astroliftManagedDomain: () => domain,
    astroliftManagedDomains: () => [{ ...domain, dnsDriver: "route53" }],
    dnsProviderDomainBinding: () => ({
      domainId: domain.id,
      domainVersion: domain.version,
      state: "CURRENT",
      connectionId: connection.id,
      connectionVersion: connection.version,
      currentConnectionVersion: connection.version,
      zoneId: DNS_SETUP.selectedZone!.id,
      zoneName: domain.zone,
      dnsWritesSupported: false,
      canVerify,
    }),
    verifyManagedDomain: () => {
      domain.verificationState = "verified";
      return {
        ok: true,
        errors: [],
        data: { zone: domain.zone, verified: true, message: "Verified" },
      };
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
    if (payload.operationName === hold)
      await new Promise<void>((resolve) => {
        release = resolve;
      });
    for (const error of result.errors ?? []) if (!refusedConnections) errors.push(error.message);
    if (loseRegistration && payload.operationName === "RegisterCloudflareZone") {
      res.destroy();
      return;
    }
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
  release?.();
  cleanup();
  client.stop();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
  expect(errors).toEqual([]);
});
function View({ domainId }: { domainId?: string }) {
  return (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <DnsConnectionClient domainId={domainId} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
}
async function start() {
  view = render(<View />);
  const button = await screen.findByRole("button", { name: "Cloudflare" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await screen.findByRole("button", { name: c.chooseConnection });
}
async function review() {
  await start();
  fireEvent.click(screen.getByRole("button", { name: c.chooseConnection }));
  const zone = await screen.findByRole("button", { name: c.selectZone });
  await waitFor(() => expect(zone).toBeEnabled());
  fireEvent.click(zone);
  await screen.findByRole("checkbox", { name: c.acknowledge });
  await waitFor(() => expect(screen.getByText("origin.acme.example")).toBeInTheDocument());
}
function accept() {
  fireEvent.click(screen.getByRole("checkbox", { name: c.acknowledge }));
  fireEvent.click(screen.getByRole("button", { name: c.register }));
}

describe("DNS connection actual SDL HttpLink journey", () => {
  it("selects exact current connection/zone, registers read-only and separately verifies current TXT", async () => {
    await review();
    expect(screen.getByText(c.noWrites)).toBeInTheDocument();
    accept();
    await screen.findByRole("button", { name: c.verify });
    await waitFor(() => expect(screen.getByRole("button", { name: c.verify })).toBeEnabled());
    expect(requests.find((r) => r.operationName === "RegisterCloudflareZone")?.variables).toEqual({
      input: {
        connectionId: connection.id,
        expectedConnectionVersion: 3,
        zoneId: DNS_SETUP.selectedZone!.id,
        zoneName: DNS_SETUP.selectedZone!.name,
      },
    });
    fireEvent.click(screen.getByRole("button", { name: c.verify }));
    await screen.findByText(c.ownershipVerified);
    expect(requests.find((r) => r.operationName === "VerifyDnsDomain")?.variables).toEqual({
      input: { zone: domain.zone },
    });
    expect(requests.some((r) => r.operationName === "CreateManagedDomain")).toBe(false);
  });
  it("does not offer verification from other action hints when exact canVerify is false", async () => {
    canVerify = false;
    await review();
    accept();
    const verify = await screen.findByRole("button", { name: c.verify });
    await waitFor(() =>
      expect(screen.queryByText(en.managedDomains.loading)).not.toBeInTheDocument()
    );
    expect(verify).toBeDisabled();
    expect(requests.some((r) => r.operationName === "VerifyDnsDomain")).toBe(false);
  });
  it("keeps incomplete zone inventory unavailable instead of selectable empty success", async () => {
    incompleteZones = true;
    await start();
    fireEvent.click(screen.getByRole("button", { name: c.chooseConnection }));
    await screen.findByText(c.inventoryIncomplete);
    expect(screen.getByRole("button", { name: c.selectZone })).toBeDisabled();
    expect(requests.some((r) => r.operationName === "CloudflareRecords")).toBe(false);
  });
  it("refuses registration from an incomplete record inventory", async () => {
    incompleteRecords = true;
    await review();
    fireEvent.click(screen.getByRole("checkbox", { name: c.acknowledge }));
    expect(screen.getByRole("button", { name: c.register })).toBeDisabled();
    expect(requests.some((r) => r.operationName === "RegisterCloudflareZone")).toBe(false);
  });
  it("keeps failed connection reads distinct from verified empty", async () => {
    refusedConnections = true;
    render(<View />);
    const cf = await screen.findByRole("button", { name: "Cloudflare" });
    await waitFor(() => expect(cf).toBeEnabled());
    fireEvent.click(cf);
    await screen.findByText("Current connection read refused");
    expect(screen.queryByText(c.connectionsEmpty)).not.toBeInTheDocument();
  });
  it("clears write-only token before submission and does not persist or return it", async () => {
    await start();
    const token = "synthetic-private-token-marker";
    fireEvent.change(screen.getByLabelText(c.connectionName), {
      target: { value: "Read-only DNS" },
    });
    fireEvent.change(screen.getByLabelText(c.token), { target: { value: token } });
    expect(screen.getByRole("button", { name: c.oauth })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: c.saveToken }));
    expect(screen.getByLabelText(c.token)).toHaveValue("");
    await screen.findByText(c.connectionSaved);
    expect(requests.find((r) => r.operationName === "ConnectCloudflareToken")?.variables).toEqual({
      input: { organizationId: identity.org, name: "Read-only DNS", token },
    });
    expect(document.body.textContent).not.toContain(token);
    expect(JSON.stringify(localStorage)).not.toContain(token);
    expect(JSON.stringify(sessionStorage)).not.toContain(token);
    expect(location.href).not.toContain(token);
  });
  it("retains accepted-but-unverified binding outcome and prevents blind repeat", async () => {
    malformed = true;
    await review();
    accept();
    await screen.findByText(c.acceptedUnverified);
    expect(screen.getByRole("button", { name: c.register })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: en.managedDomains.refresh }));
    await waitFor(() => expect(screen.getByText(c.acceptedUnverified)).toBeInTheDocument());
    expect(requests.filter((r) => r.operationName === "RegisterCloudflareZone")).toHaveLength(1);
  });
  it("keeps explicit registration refusal without advancing to ownership verification", async () => {
    explicitRefusal = true;
    await review();
    accept();
    await screen.findByText("Current registration refused");
    expect(screen.queryByRole("button", { name: c.verify })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: c.register })).toBeEnabled();
  });
  it("does not apply a held registration reply to a different actor binding", async () => {
    hold = "RegisterCloudflareZone";
    await review();
    accept();
    await waitFor(() => expect(release).not.toBeNull());
    identity.actor = "a0000000-0000-4000-8000-000000000099";
    view.rerender(<View />);
    await act(async () => release?.());
    await screen.findByRole("button", { name: "Cloudflare" });
    expect(screen.queryByText(c.registrationAccepted)).not.toBeInTheDocument();
    expect(requests.some((r) => r.operationName === "VerifyDnsDomain")).toBe(false);
  });
  it("retains an unknown transport outcome and prevents registration replay", async () => {
    loseRegistration = true;
    await review();
    accept();
    await screen.findByText(c.uncertain);
    expect(screen.getByRole("button", { name: c.register })).toBeDisabled();
    expect(requests.filter((r) => r.operationName === "RegisterCloudflareZone")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: c.verify })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: c.back }));
    fireEvent.click(screen.getByRole("button", { name: c.back }));
    fireEvent.click(screen.getByRole("button", { name: c.back }));
    expect(screen.getByRole("button", { name: "Cloudflare" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "AWS Route53" })).toBeDisabled();
  });
  it("uses a fresh saved domain/version for attachment rather than creating a new zone", async () => {
    render(<View domainId={domain.id} />);
    const cf = await screen.findByRole("button", { name: "Cloudflare" });
    await waitFor(() => expect(cf).toBeEnabled());
    fireEvent.click(cf);
    fireEvent.click(await screen.findByRole("button", { name: c.chooseConnection }));
    fireEvent.click(await screen.findByRole("button", { name: c.selectZone }));
    await screen.findByRole("checkbox", { name: c.acknowledge });
    await waitFor(() => expect(screen.getByText("origin.acme.example")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: c.acknowledge }));
    fireEvent.click(screen.getByRole("button", { name: c.attach }));
    await screen.findByText(c.registrationAccepted);
    expect(requests.find((r) => r.operationName === "AttachCloudflareZone")?.variables).toEqual({
      input: {
        connectionId: connection.id,
        expectedConnectionVersion: 3,
        zoneId: DNS_SETUP.selectedZone!.id,
        zoneName: domain.zone,
        domainId: domain.id,
        expectedDomainVersion: 7,
      },
    });
    // Fixture deliberately retains the old singular version despite accepted v8.
    // An older refresh cannot become current ownership authority.
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "ManagedDomain")).toHaveLength(2)
    );
    expect(screen.queryByRole("button", { name: c.verify })).not.toBeInTheDocument();
    expect(requests.some((r) => r.operationName === "RegisterCloudflareZone")).toBe(false);
  });
  it("re-reads support on same-org actor A-to-B-to-A and discards held connection metadata", async () => {
    hold = "DnsConnectionsPage";
    view = render(<View />);
    const cf = await screen.findByRole("button", { name: "Cloudflare" });
    await waitFor(() => expect(cf).toBeEnabled());
    fireEvent.click(cf);
    await waitFor(() => expect(release).not.toBeNull());
    const actorA = identity.actor;
    identity.actor = "a0000000-0000-4000-8000-000000000099";
    view.rerender(<View />);
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "DnsConnectionSupport")).toHaveLength(2)
    );
    identity.actor = actorA;
    view.rerender(<View />);
    await act(async () => release?.());
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "DnsConnectionSupport")).toHaveLength(3)
    );
    expect(screen.queryByRole("button", { name: c.chooseConnection })).not.toBeInTheDocument();
    expect(screen.queryByText(connection.name)).not.toBeInTheDocument();
  });
  it("uses configured Route53 domains without asking for AWS credentials or inventing catalogue", async () => {
    render(<View />);
    const aws = await screen.findByRole("button", { name: "AWS Route53" });
    await waitFor(() => expect(aws).toBeEnabled());
    fireEvent.click(aws);
    const link = await screen.findByRole("link", { name: domain.zone });
    expect(link).toHaveAttribute("href", `/domains/${domain.id}`);
    expect(screen.queryByLabelText(c.token)).not.toBeInTheDocument();
    expect(requests.some((r) => r.operationName === "CloudflareZones")).toBe(false);
  });
});
