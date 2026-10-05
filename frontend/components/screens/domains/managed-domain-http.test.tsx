import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { buildSchema, graphql } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DomainDetailClient } from "@/app/(app)/domains/[id]/domain-detail-client";
import en from "@/messages/en.json";
import { DOMAIN_ACTIVE, DOMAIN_DIAGNOSTICS } from "./domains-environments.fixtures";

const identity = vi.hoisted(() => ({ org: "", actor: "" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org, slug: "acme" }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
type Request = { operationName: string; variables: Record<string, unknown> };
let server: Server,
  client: ApolloClient,
  requests: Request[],
  failures: string[],
  domain: typeof DOMAIN_ACTIVE,
  observation: typeof DOMAIN_DIAGNOSTICS,
  missing: boolean,
  refused: boolean,
  probeForeign: boolean,
  refusedDelete: boolean,
  unknownDelete: boolean,
  canVerify: boolean,
  supportAllowed: boolean,
  supportFailure: boolean,
  probeReason: string | null,
  hold: string | null,
  release: (() => void) | null;
beforeEach(async () => {
  identity.org = "a0000000-0000-4000-8000-000000000002";
  identity.actor = "a0000000-0000-4000-8000-000000000003";
  domain = structuredClone(DOMAIN_ACTIVE);
  observation = structuredClone(DOMAIN_DIAGNOSTICS);
  requests = [];
  failures = [];
  missing = refused = probeForeign = refusedDelete = unknownDelete = false;
  canVerify = false;
  supportAllowed = true;
  supportFailure = false;
  probeReason = null;
  hold = null;
  release = null;
  const roots = {
    dnsProviderConnectionSupport: () => {
      if (supportFailure) throw new Error("Current connection support unavailable");
      return {
        allowed: supportAllowed,
        reason: supportAllowed ? "" : "PLATFORM_OPERATOR_REQUIRED",
        apiTokenSupported: supportAllowed,
        oauthConfigured: false,
        oauthSetupReason: "",
        dnsWritesSupported: false,
      };
    },
    astroliftManagedDomain: () => {
      if (refused) throw new Error("Current domain admission unavailable");
      return missing ? null : domain;
    },
    astroliftManagedDomainDiagnostics: ({ expectedVersion }: { expectedVersion: number }) => {
      if (expectedVersion !== domain.version) throw new Error("Domain version changed");
      return observation;
    },
    astroliftManagedDomainProbe: (args: Record<string, string>) => ({
      state:
        probeReason === "ICMP_TIMEOUT" ? "UNKNOWN" : args.tool === "PING" ? "UNSUPPORTED" : "OK",
      perspective: "public_dns:1.1.1.1",
      checkedAt: observation.checkedAt,
      reason:
        probeReason ?? (args.tool === "PING" ? "ICMP_TOOL_UNAVAILABLE" : "DNS_ANSWER_OBSERVED"),
      hostname: probeForeign ? "foreign.example" : args.hostname,
      tool: args.tool,
      recordType: args.recordType,
      values: probeReason ? [] : ["observed-current-answer"],
      publicAddress: null,
      httpStatus: null,
      tlsVerified: null,
      latencyMs: null,
    }),
    dnsProviderDomainBinding: () => ({
      domainId: domain.id,
      domainVersion: domain.version,
      state: "UNBOUND",
      connectionId: null,
      connectionVersion: null,
      currentConnectionVersion: null,
      zoneId: null,
      zoneName: domain.zone,
      dnsWritesSupported: false,
      canVerify,
    }),
    verifyManagedDomain: () => ({
      ok: true,
      errors: [],
      data: { zone: domain.zone, verified: true, message: "Ownership observed" },
    }),
    revalidateManagedDomain: ({ zone }: { zone: string }) => ({
      ok: true,
      errors: [],
      data: { zone, signaled: true, message: "Accepted" },
    }),
    softDeleteManagedDomain: ({ input }: { input: { id: string } }) =>
      unknownDelete
        ? {
            ok: true,
            errors: [],
            data: { id: "00000000-0000-4000-8000-000000000099", deleted: true },
          }
        : refusedDelete
          ? {
              ok: false,
              errors: [{ code: "FORBIDDEN", message: "Current removal authority refused" }],
              data: null,
            }
          : { ok: true, errors: [], data: { id: input.id, deleted: true } },
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
    for (const error of result.errors ?? []) {
      if (!refused && !supportFailure && !error.message.includes("Domain version changed"))
        failures.push(error.message);
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
  expect(failures).toEqual([]);
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
async function ready() {
  await screen.findByRole("heading", { name: DOMAIN_ACTIVE.zone });
  await screen.findByText("Mismatch");
}
async function diagnostics() {
  await ready();
  fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
}

describe("actual exported domain schema over HttpLink", () => {
  it("reads the selected GUID directly, binds diagnostics to its current version and distinguishes provisioned/mismatch", async () => {
    render(<View />);
    await ready();
    expect(screen.getByText("Provisioned")).toBeInTheDocument();
    expect(screen.getByText(en.managedDomains.mismatchHelp)).toBeInTheDocument();
    expect(requests.filter((r) => r.operationName === "ManagedDomain")).toHaveLength(1);
    expect(requests.find((r) => r.operationName === "ManagedDomainDiagnostics")?.variables).toEqual(
      { domainId: domain.id, expectedVersion: 7 }
    );
    expect(requests.some((r) => r.operationName === "ListManagedDomains")).toBe(false);
    expect(screen.queryByText("active", { exact: true })).not.toBeInTheDocument();
  });
  it("preserves an admitted shared domain without treating it as own connection configuration", async () => {
    domain.organizationSlug = null;
    observation.actions = { ...observation.actions, canDelete: false, canRevalidate: false };
    render(<View />);
    await ready();
    expect(screen.getByRole("heading", { name: domain.zone })).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: en.domainConnections.title })
    ).not.toBeInTheDocument();
    expect(requests.some((r) => r.operationName === "DnsDomainBinding")).toBe(false);
  });
  it("uses exact pending-domain canVerify without revalidation or delete authority", async () => {
    domain.verificationState = "pending";
    canVerify = true;
    observation.actions = { ...observation.actions, canDelete: false, canRevalidate: false };
    render(<View />);
    await ready();
    const button = screen.getByRole("button", { name: en.domainConnections.verify });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await screen.findByText(en.domainConnections.ownershipVerified);
    expect(requests.find((r) => r.operationName === "VerifyDnsDomain")?.variables).toEqual({
      input: { zone: domain.zone },
    });
    expect(requests.some((r) => r.operationName === "RevalidateManagedDomain")).toBe(false);
  });
  it("does not infer TXT verification from revalidation authority", async () => {
    domain.verificationState = "pending";
    canVerify = false;
    render(<View />);
    await ready();
    await waitFor(() =>
      expect(requests.some((r) => r.operationName === "DnsDomainBinding")).toBe(true)
    );
    expect(screen.getByRole("button", { name: en.domainConnections.verify })).toBeDisabled();
    expect(screen.getByRole("button", { name: en.managedDomains.revalidate })).toBeEnabled();
  });
  it("keeps a read error separate from an admitted not-found response", async () => {
    refused = true;
    const view = render(<View />);
    await screen.findByText(en.managedDomains.readFailed);
    expect(screen.queryByText(en.managedDomains.notFound)).not.toBeInTheDocument();
    refused = false;
    missing = true;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText(en.managedDomains.notFound);
    view.unmount();
  });
  it("filters only displayed records, copies expected nameservers and preserves intended routing links", async () => {
    const clipboard = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboard },
    });
    render(<View />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: `Copy ${domain.provisionNameservers[0]}` }));
    await waitFor(() => expect(clipboard).toHaveBeenCalledWith(domain.provisionNameservers[0]));
    fireEvent.click(screen.getByRole("tab", { name: "Records" }));
    fireEvent.change(screen.getByLabelText("Record type"), { target: { value: "SOA" } });
    expect(screen.getByRole("list", { name: "Records" }).children).toHaveLength(1);
    fireEvent.change(screen.getByLabelText(en.managedDomains.recordSearch), {
      target: { value: "does-not-exist" },
    });
    expect(screen.getByText(en.managedDomains.noRecords)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Routing" }));
    expect(screen.getByRole("link", { name: "Storefront" })).toHaveAttribute(
      "href",
      "/apps/storefront/domains"
    );
    expect(screen.getByText(en.managedDomains.routeHelp)).toBeInTheDocument();
  });
  it("uses actual probe variables for dig/CAA and keeps HTTPS TLS unknown when the server has no proof", async () => {
    render(<View />);
    await diagnostics();
    fireEvent.change(screen.getByLabelText("Tool"), { target: { value: "DIG" } });
    fireEvent.change(screen.getByLabelText("Record type"), { target: { value: "CAA" } });
    fireEvent.click(screen.getByRole("button", { name: "Run check" }));
    await screen.findByText("observed-current-answer");
    expect(requests.find((r) => r.operationName === "ManagedDomainProbe")?.variables).toEqual({
      domainId: domain.id,
      expectedVersion: 7,
      hostname: domain.zone,
      tool: "DIG",
      recordType: "CAA",
    });
    const result = screen.getByRole("region", { name: "Result" });
    expect(within(result).getByText("TLS verified").parentElement).toHaveTextContent("Unknown");
  });
  it("shows unsupported ping as an observation, without pretending success", async () => {
    render(<View />);
    await diagnostics();
    fireEvent.change(screen.getByLabelText("Tool"), { target: { value: "PING" } });
    fireEvent.click(screen.getByRole("button", { name: "Run check" }));
    await screen.findByText("Unsupported");
    expect(screen.getByText("ICMP_TOOL_UNAVAILABLE")).toBeInTheDocument();
  });
  it("refuses an uncorrelated probe tuple", async () => {
    probeForeign = true;
    render(<View />);
    await diagnostics();
    fireEvent.click(screen.getByRole("button", { name: "Run check" }));
    await screen.findByText(en.managedDomains.versionChanged);
    expect(screen.queryByText("observed-current-answer")).not.toBeInTheDocument();
  });
  it("discards a held probe after selection changes", async () => {
    hold = "ManagedDomainProbe";
    render(<View />);
    await diagnostics();
    fireEvent.click(screen.getByRole("button", { name: "Run check" }));
    await waitFor(() => expect(release).not.toBeNull());
    fireEvent.change(screen.getByLabelText("Record type"), { target: { value: "SRV" } });
    await act(async () => {
      release?.();
    });
    expect(screen.queryByText("observed-current-answer")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run check" })).toBeEnabled();
  });
  it("unmounts held actor-A data across A→B→A and makes independent current reads", async () => {
    hold = "ManagedDomainProbe";
    const view = render(<View />);
    await diagnostics();
    fireEvent.click(screen.getByRole("button", { name: "Run check" }));
    await waitFor(() => expect(release).not.toBeNull());
    identity.actor = "a0000000-0000-4000-8000-000000000004";
    view.rerender(<View />);
    await ready();
    identity.actor = "a0000000-0000-4000-8000-000000000003";
    view.rerender(<View />);
    await ready();
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "ManagedDomain")).toHaveLength(3)
    );
    await act(async () => {
      release?.();
    });
    fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
    expect(screen.queryByText("observed-current-answer")).not.toBeInTheDocument();
  });
  it("uses only current exact action hints and the safe cluster GUID for revalidation", async () => {
    observation.actions = { canCreate: false, canDelete: false, canRevalidate: false };
    render(<View />);
    await ready();
    expect(screen.getByRole("button", { name: "Remove domain" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Revalidate recorded DNS setup" })).toBeDisabled();
    observation.actions.canRevalidate = true;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Revalidate recorded DNS setup" })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: "Revalidate recorded DNS setup" }));
    await screen.findByText(en.managedDomains.revalidationAccepted);
    expect(requests.find((r) => r.operationName === "RevalidateManagedDomain")?.variables).toEqual({
      clusterId: domain.provisionClusterId,
      zone: domain.zone,
    });
  });
  it("requires confirmation, preserves explicit removal refusal and never claims cleanup from a foreign accepted reply", async () => {
    refusedDelete = true;
    render(<View />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Remove domain" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove domain" })
    );
    await screen.findByText("Current removal authority refused");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    refusedDelete = false;
    unknownDelete = true;
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove domain" })
    );
    await screen.findByText(en.managedDomains.acceptedUnverified);
    expect(screen.queryByText(en.managedDomains.removed)).not.toBeInTheDocument();
  });
});

it.each(["denied", "unavailable"])(
  "does not query protected binding when safe support is %s",
  async (kind) => {
    supportAllowed = false;
    supportFailure = kind === "unavailable";
    domain.verificationState = "pending";
    canVerify = true;
    render(<View />);
    await ready();
    await waitFor(() =>
      expect(requests.some((r) => r.operationName === "DnsConnectionSupport")).toBe(true)
    );
    expect(requests.some((r) => r.operationName === "DnsDomainBinding")).toBe(false);
    expect(screen.getByRole("button", { name: en.domainConnections.verify })).toBeDisabled();
    expect(screen.getByRole("heading", { name: domain.zone })).toBeInTheDocument();
  }
);

it("waits for safe current support before protected binding and disables verification after withdrawal", async () => {
  domain.verificationState = "pending";
  canVerify = true;
  hold = "DnsConnectionSupport";
  render(<View />);
  await ready();
  await waitFor(() => expect(release).not.toBeNull());
  expect(requests.some((r) => r.operationName === "DnsDomainBinding")).toBe(false);
  const verify = screen.getByRole("button", { name: en.domainConnections.verify });
  expect(verify).toBeDisabled();
  await act(async () => {
    hold = null;
    release?.();
  });
  await waitFor(() => expect(verify).toBeEnabled());
  expect(requests.filter((r) => r.operationName === "DnsDomainBinding")).toHaveLength(1);
  supportAllowed = false;
  fireEvent.click(screen.getAllByRole("button", { name: en.managedDomains.refreshCheck })[0]);
  await waitFor(() =>
    expect(requests.filter((r) => r.operationName === "DnsConnectionSupport")).toHaveLength(2)
  );
  await waitFor(() => expect(verify).toBeDisabled());
  expect(requests.filter((r) => r.operationName === "DnsDomainBinding")).toHaveLength(1);
  expect(requests.some((r) => r.operationName === "VerifyDnsDomain")).toBe(false);
});

it.each(["DNS_NO_DATA", "ICMP_TIMEOUT"])(
  "explains actual probe reason %s without a fake observed match",
  async (reason) => {
    probeReason = reason;
    render(<View />);
    await diagnostics();
    fireEvent.change(screen.getByLabelText(en.managedDomains.tool), {
      target: { value: reason === "ICMP_TIMEOUT" ? "PING" : "LOOKUP" },
    });
    fireEvent.click(screen.getByRole("button", { name: en.managedDomains.run }));
    await screen.findByText(reason);
    const response = within(screen.getByRole("region", { name: en.managedDomains.response }));
    expect(
      response.getByText(
        reason === "DNS_NO_DATA" ? en.managedDomains.dnsNoData : en.managedDomains.icmpTimeout
      )
    ).toBeInTheDocument();
    expect(response.queryByText(en.managedDomains.ok)).not.toBeInTheDocument();
    expect(
      response.getAllByText(
        reason === "DNS_NO_DATA" ? en.managedDomains.dnsNoDataStatus : en.managedDomains.unknown
      ).length
    ).toBeGreaterThan(0);
  }
);
