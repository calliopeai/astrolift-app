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
const fingerprint = "a".repeat(64);
type Request = { operationName: string; variables: Record<string, unknown> };
let server: Server,
  client: ApolloClient,
  domain: typeof DOMAIN_ACTIVE,
  facts: typeof DOMAIN_DIAGNOSTICS,
  requests: Request[],
  errors: string[],
  outcome: "accepted" | "lost" | "stale" | "foreign" | "invalid" | "equal-version",
  hold: boolean,
  release: (() => void) | null,
  refuseRead: boolean;
beforeEach(async () => {
  identity.org = "a0000000-0000-4000-8000-000000000002";
  identity.actor = "a0000000-0000-4000-8000-000000000003";
  domain = structuredClone(DOMAIN_ACTIVE);
  facts = structuredClone(DOMAIN_DIAGNOSTICS);
  facts.actions = {
    ...facts.actions,
    canUpdate: true,
    canSetDefaults: true,
    defaultUpdateReason: "",
    defaultsFingerprint: fingerprint,
  };
  requests = [];
  errors = [];
  outcome = "accepted";
  hold = refuseRead = false;
  release = null;
  const roots = {
    // This install withholds nothing (calliope-installer#447).
    astroliftWithheldCapabilities: () => [],
    astroliftManagedDomain: () => {
      if (refuseRead) throw new Error("Current admission refused");
      return domain;
    },
    astroliftManagedDomainDiagnostics: ({ expectedVersion }: { expectedVersion: number }) => {
      if (expectedVersion !== domain.version) throw new Error("Current version changed");
      return { ...facts, id: domain.id, zone: domain.zone, version: domain.version };
    },
    dnsProviderConnectionSupport: () => ({
      allowed: false,
      reason: "PLATFORM_OPERATOR_REQUIRED",
      apiTokenSupported: false,
      oauthConfigured: false,
      oauthSetupReason: "",
      dnsWritesSupported: false,
    }),
    updateManagedDomain: ({
      input,
    }: {
      input: { defaultFor: typeof DOMAIN_ACTIVE.defaultFor };
    }) => {
      if (outcome === "stale")
        return {
          ok: false,
          errors: [
            {
              code: "VERSION_MISMATCH",
              message: "MANAGED_DOMAIN_DEFAULTS_CHANGED",
              field: "expectedDefaultsFingerprint",
            },
          ],
          data: null,
        };
      domain = { ...domain, defaultFor: input.defaultFor, version: domain.version + 1 };
      facts.actions.defaultsFingerprint = "b".repeat(64);
      facts.checks.push({
        key: "effective_tenant_apps_domain",
        state: "OK",
        perspective: "current_metadata",
        checkedAt: facts.checkedAt,
        reason: "REGISTERED_DOMAIN_EFFECTIVE_DEFAULT",
        expected: [],
        observed: [domain.zone],
      });
      return {
        ok: outcome === "invalid" ? null : true,
        errors: [],
        data: {
          ...domain,
          version: outcome === "equal-version" ? domain.version - 1 : domain.version,
          id: outcome === "foreign" ? "a0000000-0000-4000-8000-000000000099" : domain.id,
        },
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
    if (payload.operationName === "UpdateManagedDomainDefaults" && hold)
      await new Promise<void>((resolve) => {
        release = resolve;
      });
    for (const error of result.errors ?? [])
      if (
        !refuseRead &&
        !error.message.includes("Current version changed") &&
        outcome !== "invalid"
      )
        errors.push(error.message);
    if (payload.operationName === "UpdateManagedDomainDefaults" && outcome === "lost") {
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
function View() {
  return (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <DomainDetailClient id={domain.id} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
}
function section() {
  return screen.getByRole("heading", { name: en.domainDefaults.title }).closest("section")!;
}
function writes() {
  return requests.filter((r) => r.operationName === "UpdateManagedDomainDefaults");
}
async function ready() {
  await screen.findByRole("heading", { name: en.domainDefaults.title });
  await waitFor(() => expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeEnabled());
}
async function submit(choice = "tenant_apps") {
  fireEvent.change(screen.getByLabelText(en.domainDefaults.useFor), { target: { value: choice } });
  fireEvent.click(screen.getByRole("button", { name: en.domainDefaults.review }));
  fireEvent.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: en.domainDefaults.save,
    })
  );
}
describe("default review through actual schema HttpLink", () => {
  it("submits exact version and impact fingerprint and refreshes effective metadata without routing writes", async () => {
    render(<View />);
    await ready();
    await submit();
    await screen.findByText(en.domainDefaults.applied);
    await waitFor(() => expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeEnabled());
    expect(writes()).toHaveLength(1);
    expect(writes()[0].variables).toEqual({
      input: {
        id: DOMAIN_ACTIVE.id,
        defaultFor: "tenant_apps",
        expectedVersion: 7,
        expectedDefaultsFingerprint: fingerprint,
        dnsConfig: null,
        isWildcardManaged: null,
      },
    });
    expect(domain.version).toBe(8);
    expect(within(section()).getAllByText(domain.zone)).toHaveLength(2);
    expect(
      requests.some(
        (r) => r.operationName === "ManagedDomainDiagnostics" && r.variables.expectedVersion === 8
      )
    ).toBe(true);
    expect(
      requests.some((r) => ["VerifyDnsDomain", "RevalidateManagedDomain"].includes(r.operationName))
    ).toBe(false);
  });
  it("never replays a lost accepted write; explicit refresh and another review are required", async () => {
    outcome = "lost";
    render(<View />);
    await ready();
    await submit();
    await screen.findAllByText(en.domainDefaults.uncertain);
    await waitFor(() =>
      expect(
        within(section()).getByRole("button", { name: en.managedDomains.refresh })
      ).toBeEnabled()
    );
    expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeDisabled();
    expect(writes()).toHaveLength(1);
    outcome = "accepted";
    fireEvent.click(within(section()).getByRole("button", { name: en.managedDomains.refresh }));
    await waitFor(() => expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeEnabled());
    expect(writes()).toHaveLength(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await submit("preview_envs");
    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(writes()[1].variables).toMatchObject({
      input: { expectedVersion: 8, expectedDefaultsFingerprint: "b".repeat(64) },
    });
  });
  it("refuses stale impact with localized explanation and a held new-write gate", async () => {
    outcome = "stale";
    render(<View />);
    await ready();
    await submit();
    await screen.findByText(en.domainDefaults.reasonChanged);
    expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeDisabled();
    expect(screen.queryByText(en.domainDefaults.applied)).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(domain.version).toBe(7);
  });
  it("retains accepted-but-unverified foreign data without asserting success", async () => {
    outcome = "foreign";
    render(<View />);
    await ready();
    await submit();
    await screen.findByText(en.managedDomains.acceptedUnverified);
    expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeDisabled();
    expect(writes()).toHaveLength(1);
  });
  it("holds accepted-unverified when the acknowledgement has the unchanged reviewed version", async () => {
    outcome = "equal-version";
    render(<View />);
    await ready();
    await submit();
    await screen.findByText(en.managedDomains.acceptedUnverified);
    await waitFor(() =>
      expect(
        within(section()).getByRole("button", { name: en.managedDomains.refresh })
      ).toBeEnabled()
    );
    expect(domain.version).toBe(8);
    expect(screen.queryByText(en.domainDefaults.applied)).not.toBeInTheDocument();
    expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeDisabled();
    expect(writes()).toHaveLength(1);
    fireEvent.click(within(section()).getByRole("button", { name: en.managedDomains.refresh }));
    await waitFor(() => expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeEnabled());
    expect(writes()).toHaveLength(1);
  });
  it("holds uncertainty on malformed envelope", async () => {
    outcome = "invalid";
    render(<View />);
    await ready();
    await submit();
    await screen.findAllByText(en.domainDefaults.uncertain);
    expect(writes()).toHaveLength(1);
    expect(screen.queryByText(en.domainDefaults.applied)).not.toBeInTheDocument();
  });
  it.each(["READ_ONLY_DOMAIN", "ORGANIZATION_DEFAULT_OVERRIDE", "PERMISSION_DENIED"])(
    "fails closed for current %s",
    async (reason) => {
      facts.actions = {
        ...facts.actions,
        canUpdate: false,
        canSetDefaults: false,
        defaultUpdateReason: reason,
      };
      render(<View />);
      await screen.findByText(reason);
      expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeDisabled();
      expect(screen.getByRole("button", { name: en.domainDefaults.review })).toBeDisabled();
      expect(writes()).toHaveLength(0);
      expect(requests.some((r) => r.operationName === "DnsDomainBinding")).toBe(false);
    }
  );
  it("allows removing flags independently of unavailable non-NONE admission", async () => {
    facts.actions.canSetDefaults = false;
    facts.actions.defaultUpdateReason = "READ_ONLY_DOMAIN";
    render(<View />);
    await ready();
    fireEvent.change(screen.getByLabelText(en.domainDefaults.useFor), {
      target: { value: "both" },
    });
    expect(screen.getByRole("button", { name: en.domainDefaults.review })).toBeDisabled();
    await submit("none");
    await screen.findByText(en.domainDefaults.applied);
    expect(writes()[0].variables).toMatchObject({ input: { defaultFor: "none" } });
  });
  it("does not leak a held write result across actual actor A→B→A remount", async () => {
    hold = true;
    const view = render(<View />);
    await ready();
    await submit();
    await waitFor(() => expect(release).not.toBeNull());
    identity.actor = "a0000000-0000-4000-8000-000000000004";
    view.rerender(<View />);
    await ready();
    identity.actor = "a0000000-0000-4000-8000-000000000003";
    view.rerender(<View />);
    await ready();
    await act(async () => {
      release?.();
    });
    expect(screen.queryByText(en.domainDefaults.applied)).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
  });
  it("blocks duplicate submission while the original HTTP write is held", async () => {
    hold = true;
    render(<View />);
    await ready();
    await submit();
    await waitFor(() => expect(release).not.toBeNull());
    expect(screen.getByLabelText(en.domainDefaults.useFor)).toBeDisabled();
    expect(writes()).toHaveLength(1);
    await act(async () => {
      release?.();
    });
    await screen.findByText(en.domainDefaults.applied);
  });
  it("does not enable further writes when current read admission withdraws after acceptance", async () => {
    hold = true;
    render(<View />);
    await ready();
    await submit();
    await waitFor(() => expect(release).not.toBeNull());
    refuseRead = true;
    await act(async () => {
      release?.();
    });
    await screen.findByText("Current admission refused");
    expect(screen.queryByLabelText(en.domainDefaults.useFor)).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
  });
  it("does not dispatch without a valid current impact fingerprint", async () => {
    facts.actions.defaultsFingerprint = "opaque-unbound";
    render(<View />);
    await screen.findByRole("heading", { name: en.domainDefaults.title });
    expect(screen.getByRole("button", { name: en.domainDefaults.review })).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });
});
