import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { buildSchema, graphql } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import en from "@/messages/en.json";
import { DomainEmailClient } from "@/app/(app)/domains/[id]/email/domain-email-client";
import { SendTestEmailDialog } from "@/app/(app)/apps/[slug]/components/managed-services-summary-card";
import {
  DOMAIN_ACTIVE,
  DOMAIN_DIAGNOSTICS,
} from "@/components/screens/domains/domains-environments.fixtures";
import { EMAIL } from "@/components/screens/apps/overview/app-overview-cards-b.fixtures";
import { EmailDeliveryClient } from "./EmailDeliveryClient";
import { DELIVERY_PANEL, DELIVERY_TEST } from "./EmailDeliveryPanel.stories";
const identity = vi.hoisted(() => ({ actor: "", org: "" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org, slug: "acme" }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
const c = en.emailDelivery,
  id = DELIVERY_TEST.managedServiceId;
let server: Server,
  client: ApolloClient,
  view: ReturnType<typeof render>,
  support: typeof DELIVERY_PANEL.support,
  records: (typeof DELIVERY_TEST)[],
  requests: { operationName: string; variables: Record<string, unknown> }[],
  schemaErrors: string[];
let loseReply: boolean,
  sourceRefusal: boolean,
  forged: boolean,
  readRefusal: boolean,
  hold: string | null,
  release: (() => void) | null;
beforeEach(async () => {
  vi.stubGlobal("crypto", webcrypto);
  sessionStorage.clear();
  identity.actor = "a0000000-0000-4000-8000-000000000001";
  identity.org = "a0000000-0000-4000-8000-000000000002";
  support = structuredClone(DELIVERY_PANEL.support);
  records = [];
  requests = [];
  schemaErrors = [];
  loseReply = sourceRefusal = forged = readRefusal = false;
  hold = null;
  release = null;
  const apps = [
    {
      id: "b0000000-0000-4000-8000-000000000001",
      slug: "storefront",
      name: "Storefront",
      organizationSlug: "acme",
      version: 1,
    },
  ];
  const services = [
    {
      id,
      name: "Application mail",
      kind: "email",
      variant: "ses",
      status: "active",
      environmentName: "production",
      registeredAppSlug: "storefront",
    },
  ];
  const roots = {
    astroliftManagedDomain: () => DOMAIN_ACTIVE,
    astroliftManagedDomainDiagnostics: () => ({
      ...DOMAIN_DIAGNOSTICS,
      id: DOMAIN_ACTIVE.id,
      version: DOMAIN_ACTIVE.version,
      zone: DOMAIN_ACTIVE.zone,
    }),
    dnsProviderDomainBinding: () => null,
    astroliftAppsPage: () => ({ items: apps, nextCursor: null, totalCount: 1 }),
    astroliftApp: () => apps[0],
    astroliftManagedServicesPage: () => ({ items: services, nextCursor: null, totalCount: 1 }),
    astroliftManagedDomainProbe: ({
      hostname,
      recordType,
      tool,
    }: {
      hostname: string;
      recordType: string;
      tool: string;
    }) => ({
      state: "OK",
      hostname,
      recordType,
      tool,
      perspective: "controlled_dns",
      checkedAt: DELIVERY_TEST.createdAt,
      reason: "CONTROLLED_DNS",
      values: ["controlled-record"],
      publicAddress: null,
      httpStatus: null,
      tlsVerified: null,
      latencyMs: 1,
    }),
    emailDeliveryTestSupport: () => support,
    emailDeliveryTestsPage: ({ after }: { after: string | null }) => {
      if (readRefusal) throw new Error("Current history denied");
      return {
        items: after ? records.slice(1) : records,
        nextCursor: null,
        totalCount: records.length,
      };
    },
    sendEmailDeliveryTest: ({
      input,
    }: {
      input: {
        managedServiceId: string;
        expectedVersion: number;
        requestId: string;
        recipient: string;
      };
    }) => {
      const prior = records.find((row) => row.requestId === input.requestId);
      const row = prior ?? {
        ...DELIVERY_TEST,
        requestId: input.requestId,
        recipient: input.recipient,
        sender: support!.sender!,
        identity: support!.identity!,
        accountId: support!.accountId!,
        region: support!.region!,
        simulator: false,
        eventTrackingConfigured: false,
      };
      if (!prior) records.unshift(row);
      if (sourceRefusal)
        return {
          ok: false,
          errors: [{ code: "PRECONDITION", message: "EMAIL_SOURCE_CHANGED" }],
          data: null,
        };
      return {
        ok: true,
        errors: [],
        data: forged ? { ...row, managedServiceId: "d0000000-0000-4000-8000-000000000099" } : row,
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
    for (const error of result.errors ?? []) if (!readRefusal) schemaErrors.push(error.message);
    if (loseReply && payload.operationName === "SendEmailDeliveryTest") {
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
  vi.unstubAllGlobals();
  expect(schemaErrors).toEqual([]);
});
function node(mode: "panel" | "domain" | "quick" = "panel") {
  return (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        {mode === "domain" ? (
          <DomainEmailClient id={DOMAIN_ACTIVE.id} />
        ) : mode === "quick" ? (
          <SendTestEmailDialog svc={{ ...EMAIL, id }} open onOpenChange={() => {}} />
        ) : (
          <EmailDeliveryClient id={id} name="Application mail" />
        )}
      </NextIntlClientProvider>
    </ApolloProvider>
  );
}
async function start() {
  view = render(node());
  await waitFor(() => expect(screen.getByText("sender@acme.example")).toBeInTheDocument());
}
async function review() {
  fireEvent.change(screen.getByLabelText(c.recipient), {
    target: { value: "recipient@acme.example" },
  });
  fireEvent.change(screen.getByLabelText(`${c.subject} (${c.optional})`), {
    target: { value: "private-subject-canary" },
  });
  fireEvent.change(screen.getByLabelText(`${c.body} (${c.optional})`), {
    target: { value: "private-body-canary" },
  });
  await waitFor(() => expect(screen.getByRole("button", { name: c.review })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: c.review }));
  await waitFor(() => expect(screen.getByText(c.reviewed)).toBeInTheDocument());
}
const writes = () => requests.filter((r) => r.operationName === "SendEmailDeliveryTest");
describe("actual mounted SDL email HttpLink boundary", () => {
  it.each([
    [`sender@mail.${DOMAIN_ACTIVE.zone}`, "match"],
    [`sender@lookalike${DOMAIN_ACTIVE.zone}`, "different"],
    [`sender@${DOMAIN_ACTIVE.zone}.foreign.example`, "different"],
  ] as const)(
    "classifies sender-domain boundary %s without claiming delivery",
    async (sender, label) => {
      support = { ...support!, sender };
      view = render(node("domain"));
      await waitFor(() =>
        expect(screen.getByRole("option", { name: "Storefront" })).toBeInTheDocument()
      );
      fireEvent.change(screen.getByLabelText(c.apps), {
        target: { value: "b0000000-0000-4000-8000-000000000001" },
      });
      await waitFor(() =>
        expect(
          screen.getByRole("option", { name: "Application mail / production (ses)" })
        ).toBeInTheDocument()
      );
      fireEvent.change(screen.getByLabelText(c.services), { target: { value: id } });
      await waitFor(() => expect(screen.getByText(c[label])).toBeInTheDocument());
      expect(writes()).toHaveLength(0);
    }
  );

  it("uses the existing quick action with explicit source review, keeping acceptance visible", async () => {
    view = render(node("quick"));
    await waitFor(() => expect(screen.getByText("sender@acme.example")).toBeInTheDocument());
    await review();
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    await waitFor(() => expect(screen.getByText(c.acceptedHelp)).toBeInTheDocument());
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(requests.some((row) => row.operationName === "SendManagedServiceTestEmail")).toBe(false);
  });
  it("connects the domain entry to current app/service reads and exact versioned mail DNS probes", async () => {
    view = render(node("domain"));
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Storefront" })).toBeInTheDocument()
    );
    fireEvent.change(screen.getByLabelText(c.apps), {
      target: { value: "b0000000-0000-4000-8000-000000000001" },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("option", { name: "Application mail / production (ses)" })
      ).toBeInTheDocument()
    );
    fireEvent.change(screen.getByLabelText(c.services), { target: { value: id } });
    await waitFor(() => expect(screen.getByText("sender@acme.example")).toBeInTheDocument());
    expect(screen.getByText(c.different)).toBeInTheDocument();
    for (const [label, hostname, type] of [
      [c.mx, DOMAIN_ACTIVE.zone, "MX"],
      [c.spf, DOMAIN_ACTIVE.zone, "TXT"],
      [c.dmarc, `_dmarc.${DOMAIN_ACTIVE.zone}`, "TXT"],
    ]) {
      fireEvent.click(screen.getByRole("button", { name: label }));
      await waitFor(() =>
        expect(
          requests.findLast((row) => row.operationName === "ManagedDomainProbe")?.variables
        ).toMatchObject({
          domainId: DOMAIN_ACTIVE.id,
          expectedVersion: 7,
          hostname,
          recordType: type,
          tool: "LOOKUP",
        })
      );
      await waitFor(() => expect(screen.getByRole("button", { name: label })).toBeEnabled());
    }
    expect(screen.getByRole("button", { name: c.dkim })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(c.selector), { target: { value: "reviewed-selector" } });
    fireEvent.change(screen.getByLabelText(c.dkimRecordType), { target: { value: "CNAME" } });
    fireEvent.click(screen.getByRole("button", { name: c.dkim }));
    await waitFor(() =>
      expect(
        requests.findLast((row) => row.operationName === "ManagedDomainProbe")?.variables.hostname
      ).toBe(`reviewed-selector._domainkey.${DOMAIN_ACTIVE.zone}`)
    );
    expect(
      requests.filter((row) => row.operationName === "ManagedDomainProbe").at(-1)?.variables
        .recordType
    ).toBe("CNAME");
    await review();
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(
      requests.find((row) => row.operationName === "EmailDeliveryServices")?.variables.appSlug
    ).toBe("storefront");
  });

  it.each([
    { subject: "é".repeat(101), body: "valid" },
    { subject: "valid", body: "é".repeat(4097) },
    { subject: "line\x01break", body: "valid" },
    { subject: "bad\ud800", body: "valid" },
    { subject: "valid", body: "bad\udfff" },
  ])("keeps invalid content editable without a nonce or dispatch: %j", async (values) => {
    await start();
    fireEvent.change(screen.getByLabelText(c.recipient), {
      target: { value: "recipient@acme.example" },
    });
    fireEvent.change(screen.getByLabelText(`${c.subject} (${c.optional})`), {
      target: { value: values.subject },
    });
    fireEvent.change(screen.getByLabelText(`${c.body} (${c.optional})`), {
      target: { value: values.body },
    });
    expect(screen.getByText(c.invalidContent)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: c.review })).toBeDisabled();
    expect(screen.getByLabelText(c.recipient)).toBeEnabled();
    expect(writes()).toHaveLength(0);
    expect(sessionStorage.length).toBe(0);
    fireEvent.change(screen.getByLabelText(`${c.subject} (${c.optional})`), {
      target: { value: "é".repeat(100) },
    });
    fireEvent.change(screen.getByLabelText(`${c.body} (${c.optional})`), {
      target: { value: "é".repeat(4096) },
    });
    fireEvent.click(screen.getByRole("button", { name: c.review }));
    await waitFor(() => expect(screen.getByRole("button", { name: c.send })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(writes()).toHaveLength(1));
  });

  it("sends only after current exact-source review and distinguishes acceptance from delivery", async () => {
    await start();
    expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
    await review();
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    await waitFor(() => expect(screen.getByText(c.acceptedHelp)).toBeInTheDocument());
    expect(writes()[0].variables.input).toMatchObject({
      managedServiceId: id,
      expectedVersion: 7,
      recipient: "recipient@acme.example",
    });
    expect(screen.getByText(c.noTracking)).toBeInTheDocument();
    expect(screen.queryByText(c.delivered)).not.toBeInTheDocument();
    const stored = Object.values(sessionStorage);
    expect(JSON.stringify(stored)).not.toContain("private-subject-canary");
    expect(JSON.stringify(stored)).not.toContain("private-body-canary");
  });
  it("disables source-denied sends without interpreting missing support as permission", async () => {
    support = { ...support!, allowed: false, reason: "EXACT_TEST_TRANSPORT_UNSUPPORTED" };
    await start();
    expect(screen.getByText(c.denied)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
    expect(writes()).toEqual([]);
  });
  it("invalidates recipient review when the current service version is refreshed", async () => {
    await start();
    await review();
    support = { ...support!, serviceVersion: 8 };
    fireEvent.click(screen.getByRole("button", { name: c.refresh }));
    await waitFor(() => expect(screen.getByText("8")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
    expect(writes()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: c.review }));
    await waitFor(() => expect(screen.getByRole("button", { name: c.send })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].variables.input).toMatchObject({ expectedVersion: 8 });
  });
  it("retains exactly the same nonce and content after a lost reply, without duplicating native intent", async () => {
    await start();
    await review();
    loseReply = true;
    readRefusal = true;
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(screen.getByText(c.uncertain)).toBeInTheDocument());
    const input = writes()[0].variables.input;
    expect(screen.getByLabelText(c.recipient)).toBeDisabled();
    loseReply = false;
    fireEvent.click(screen.getByRole("button", { name: c.retry }));
    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(writes()[1].variables.input).toEqual(input);
    await waitFor(() => expect(screen.getByText(c.acceptedHelp)).toBeInTheDocument());
    expect(records).toHaveLength(1);
  });
  it("recovers durable acceptance after source-change refusal rather than claiming no send happened", async () => {
    await start();
    await review();
    sourceRefusal = true;
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(screen.getByText("EMAIL_SOURCE_CHANGED")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(c.acceptedHelp)).toBeInTheDocument());
    expect(writes()).toHaveLength(1);
    expect(records).toHaveLength(1);
    support = { ...support!, allowed: false, reason: "EMAIL_VERIFIED_ACCOUNT_MISMATCH" };
    fireEvent.click(screen.getByRole("button", { name: c.refresh }));
    await waitFor(() => expect(screen.getByText(c.denied)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: c.retry })).toBeDisabled();
    expect(screen.queryByRole("button", { name: c.startAnother })).not.toBeInTheDocument();
  });
  it("keeps accepted metadata uncertainty when a successful response belongs to another service", async () => {
    await start();
    await review();
    forged = true;
    readRefusal = true;
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(screen.getByText(c.acceptedUnverified)).toBeInTheDocument());
    expect(screen.queryByText(c.acceptedHelp)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: c.startAnother })).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(sessionStorage.length).toBe(1);
  });
  it("reopens an uncertain request without storing its body, and permits only byte-exact intent replay", async () => {
    await start();
    await review();
    loseReply = true;
    readRefusal = true;
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(screen.getByText(c.uncertain)).toBeInTheDocument());
    const input = writes()[0].variables.input;
    view.unmount();
    loseReply = false;
    view = render(node());
    await waitFor(() => expect(screen.getByText(c.recoverHelp)).toBeInTheDocument());
    expect(screen.getByLabelText(c.recipient)).toBeEnabled();
    fireEvent.change(screen.getByLabelText(c.recipient), {
      target: { value: "different@acme.example" },
    });
    fireEvent.click(screen.getByRole("button", { name: c.review }));
    expect(screen.getByRole("button", { name: c.retry })).toBeDisabled();
    expect(writes()).toHaveLength(1);
    await review();
    fireEvent.click(screen.getByRole("button", { name: c.retry }));
    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(writes()[1].variables.input).toEqual(input);
    expect(records).toHaveLength(1);
  });
  it("rejects a foreign history row instead of showing it as authorized current metadata", async () => {
    records = [
      {
        ...DELIVERY_TEST,
        managedServiceId: "e0000000-0000-4000-8000-000000000001",
        recipient: "foreign-private-recipient@example.test",
      },
    ];
    await start();
    await waitFor(() => expect(screen.getByText(c.historyUnavailable)).toBeInTheDocument());
    expect(screen.queryByText("foreign-private-recipient@example.test")).not.toBeInTheDocument();
  });
  it("refuses a held reply across a same-org actor A to B to A transition", async () => {
    await start();
    await review();
    hold = "SendEmailDeliveryTest";
    readRefusal = true;
    fireEvent.click(screen.getByRole("button", { name: c.send }));
    await waitFor(() => expect(release).not.toBeNull());
    identity.actor = "b0000000-0000-4000-8000-000000000001";
    view.rerender(node());
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "EmailDeliverySupport")).toHaveLength(2)
    );
    identity.actor = "a0000000-0000-4000-8000-000000000001";
    view.rerender(node());
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "EmailDeliverySupport")).toHaveLength(3)
    );
    await act(async () => {
      release?.();
    });
    await waitFor(() => expect(screen.getByText(c.recoverHelp)).toBeInTheDocument());
    expect(screen.queryByText(c.acceptedHelp)).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
  });
  it("refuses a new nonce when existing recovery metadata is corrupt", async () => {
    sessionStorage.setItem(
      `astrolift.email-test.v1:${JSON.stringify([identity.actor, identity.org, id])}`,
      "{invalid"
    );
    await start();
    expect(screen.getByText(c.uncertain)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(c.recipient), {
      target: { value: "recipient@acme.example" },
    });
    expect(screen.getByRole("button", { name: c.review })).toBeDisabled();
    expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
    expect(writes()).toEqual([]);
  });
});
