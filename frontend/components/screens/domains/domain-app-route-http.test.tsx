import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { buildSchema, graphql } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { DomainAppConnectionClient } from "./DomainAppConnectionClient";
import type { DomainAppRouteContext } from "./use-domain-app-route";
import type {
  DomainAppRouteOperationFieldsFragment as Operation,
  DomainAppRouteReviewQuery,
  DomainAppRouteTargetsQuery,
} from "@/graphql/__generated__/domain-app-route";
const schema = buildSchema(
  readFileSync(
    process.env.ASTROLIFT_DOMAIN_ROUTE_SCHEMA ?? `${process.cwd()}/schema.graphql`,
    "utf8"
  )
);
const id = (n: number) => `a0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
let server: Server,
  client: ApolloClient,
  requests: { operationName: string; variables: Record<string, unknown> }[],
  errors: string[],
  allowed: boolean,
  wrong: string | null,
  lost: boolean,
  hold: boolean,
  release: (() => void) | null,
  row: Operation | null;
let target: DomainAppRouteTargetsQuery["managedDomainAppTargetsPage"]["items"][number],
  review: DomainAppRouteReviewQuery["managedDomainAppRouteReview"],
  context: DomainAppRouteContext,
  after: boolean;
const reload = vi.fn(async () => true);
beforeEach(async () => {
  sessionStorage.clear();
  requests = [];
  errors = [];
  allowed = true;
  wrong = null;
  lost = hold = after = false;
  release = null;
  row = null;
  reload.mockClear();
  context = {
    actor: id(1),
    organization: id(2),
    domainId: id(3),
    domainVersion: 4,
    zone: "example.test",
    metadataCurrent: true,
    onReload: reload,
  };
  target = {
    kind: "FAAS_HTTP_API",
    appId: id(4),
    appName: "Landing",
    appVersion: 5,
    environmentId: id(5),
    environmentName: "Production",
    environmentVersion: 6,
    workloadId: id(6),
    workloadName: "Landing",
    workloadVersion: 7,
    clusterId: id(7),
    clusterName: "AWS",
    clusterVersion: 8,
  };
  review = {
    kind: target.kind,
    domainId: context.domainId,
    domainVersion: 4,
    environmentId: target.environmentId,
    environmentVersion: 6,
    workloadId: target.workloadId,
    workloadVersion: 7,
    clusterId: target.clusterId,
    clusterVersion: 8,
    hostname: context.zone,
    reviewSha256: "a".repeat(64),
    writer: "ROUTE53_ALIAS",
    records: [{ name: context.zone, type: "A_ALIAS", target: null, targetZoneId: null }],
    certificateStatus: "ISSUED",
    allocationState: "PENDING_NATIVE_DOMAIN",
    apiId: "abc123",
    apiEndpoint: "https://abc123.execute-api.us-west-2.amazonaws.com",
    stage: "$default",
    functionArn: "arn:aws:lambda:us-west-2:123456789012:function:landing",
    sourceFingerprint: "b".repeat(64),
    canPrepare: false,
    canAttach: true,
    reason: "ROUTE_REVIEWED",
  };
  const roots = {
    managedDomainAppRouteSupport: () => ({
      domainId: context.domainId,
      version: context.domainVersion,
      allowed,
      reason: allowed ? "" : "CURRENT_ROUTE_AUTHORITY_UNAVAILABLE",
      writer: "ROUTE53_ALIAS",
    }),
    managedDomainAppTargetsPage: () => ({
      items: [after ? { ...target, workloadId: id(10), workloadName: "Other" } : target],
      nextCursor: after ? null : "next",
      hasNextPage: !after,
    }),
    managedDomainAppRouteReview: () =>
      wrong
        ? {
            ...review,
            [wrong]: wrong.endsWith("Version") ? 999 : wrong === "kind" ? "DEPLOYMENT" : id(99),
          }
        : review,
    managedDomainAppRouteOperation: ({ input }: { input: { requestId: string } }) =>
      row?.requestId === input.requestId ? row : null,
    connectManagedDomainAppRoute: ({ input }: { input: Record<string, unknown> }) =>
      record(input, "connect"),
    prepareManagedDomainAppRoute: ({ input }: { input: Record<string, unknown> }) =>
      record(input, "prepare"),
    reconcileManagedDomainAppRoute: ({
      input,
    }: {
      input: { requestId: string; expectedVersion: number };
    }) => {
      if (!row) throw new Error("Missing original");
      row = { ...row, version: input.expectedVersion + 1, status: "pending" };
      return { ok: true, errors: [], data: row };
    },
  };
  function record(input: Record<string, unknown>, purpose: string) {
    const stored = JSON.parse(
      sessionStorage.getItem(
        `astrolift.domain-route.v1:${context.actor}:${context.organization}:${context.domainId}`
      )!
    );
    expect(stored.input.requestId).toBe(input.requestId);
    expect(stored.input.reviewSha256).toBe(input.reviewSha256);
    row = {
      id: id(8),
      version: 1,
      requestId: String(input.requestId),
      domainId: String(input.domainId),
      environmentId: String(input.environmentId),
      workloadId: String(input.workloadId),
      customDomainId: purpose === "connect" ? id(9) : null,
      hostname: String(input.hostname),
      purpose,
      status: "accepted",
      reason: "WORKFLOW_DISPATCH_UNCONFIRMED",
      dnsState: "unknown",
      ingressState: "unknown",
      tlsState: "unknown",
      canReconcile: true,
    };
    return { ok: true, errors: [], data: wrong === "reply" ? { ...row, requestId: id(99) } : row };
  }
  server = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const payload = JSON.parse(body);
    requests.push(payload);
    if (payload.operationName === "DomainAppRouteTargets")
      after = payload.variables.after === "next";
    const result = await graphql({
      schema,
      source: payload.query,
      variableValues: payload.variables,
      rootValue: roots,
    });
    for (const e of result.errors ?? []) errors.push(e.message);
    if (payload.operationName === "DomainAppRouteReview" && hold)
      await new Promise<void>((r) => {
        release = r;
      });
    if (payload.operationName === "ConnectDomainAppRoute" && lost) {
      res.destroy();
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(result));
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
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
  vi.unstubAllGlobals();
  cleanup();
  client.stop();
  server.closeAllConnections();
  await new Promise<void>((r) => server.close(() => r()));
  expect(errors).toEqual([]);
});
function View({ ctx = context }: { ctx?: DomainAppRouteContext }) {
  return (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <DomainAppConnectionClient context={ctx} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
}
const writes = () =>
  requests.filter((r) =>
    ["ConnectDomainAppRoute", "PrepareDomainAppRoute", "ReconcileDomainAppRoute"].includes(
      r.operationName
    )
  );
async function open() {
  fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.connect }));
  await screen.findByRole("dialog");
}
async function choose() {
  await open();
  fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.next }));
  await waitFor(() => expect(screen.getByLabelText(en.domainAppRouting.targetStep)).toBeEnabled());
  fireEvent.change(screen.getByLabelText(en.domainAppRouting.targetStep), {
    target: { value: `${target.environmentId}:${target.workloadId}` },
  });
  fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.review }));
}
async function ready() {
  await choose();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: en.domainAppRouting.attach })).toBeEnabled()
  );
}
function intent() {
  const key = `astrolift.domain-route.v1:${context.actor}:${context.organization}:${context.domainId}`;
  return JSON.parse(sessionStorage.getItem(key)!);
}
describe("managed-domain app routes over actual draft SDL HttpLink", () => {
  it("retains manual discovery without enabling managed writes even if conflicting flags are returned", async () => {
    review = { ...review, writer: "MANUAL", canPrepare: true, canAttach: true };
    render(<View />);
    await choose();
    await screen.findByText(en.domainAppRouting.manualHelp);
    expect(screen.getByRole("button", { name: en.domainAppRouting.prepare })).toBeDisabled();
    expect(screen.getByRole("button", { name: en.domainAppRouting.attach })).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });

  it("pins original target, versions, reviewed digest and UUID before a pending native allocation", async () => {
    render(<View />);
    await ready();
    expect(screen.getByText(en.domainAppRouting.targetPending)).toBeVisible();
    expect(screen.queryByText(/A_ALIAS →/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(intent().input).toEqual({
      domainId: id(3),
      expectedDomainVersion: 4,
      environmentId: id(5),
      expectedEnvironmentVersion: 6,
      workloadId: id(6),
      hostname: "example.test",
      reviewSha256: "a".repeat(64),
      requestId: row!.requestId,
    });
    await screen.findByText("WORKFLOW_DISPATCH_UNCONFIRMED");
    expect(screen.getByText(en.domainAppRouting.routeObservationHelp)).toBeVisible();
  });
  it.each([
    "domainId",
    "domainVersion",
    "environmentId",
    "environmentVersion",
    "workloadId",
    "workloadVersion",
    "clusterId",
    "clusterVersion",
    "kind",
  ])("refuses uncorrelated %s review before persistence/effects", async (field) => {
    wrong = field;
    render(<View />);
    await choose();
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: en.domainAppRouting.attach })).toBeDisabled();
    expect(writes()).toHaveLength(0);
    expect(sessionStorage.length).toBe(0);
  });
  it("does not read target inventory when current support refuses", async () => {
    allowed = false;
    render(<View />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.next }));
    await screen.findByText("CURRENT_ROUTE_AUTHORITY_UNAVAILABLE");
    expect(requests.some((r) => r.operationName === "DomainAppRouteTargets")).toBe(false);
    expect(writes()).toHaveLength(0);
  });
  it("retains the original UUID after lost reply and reload; recovery never resends Connect", async () => {
    lost = true;
    const view = render(<View />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
    await screen.findByText(en.domainAppRouting.unknownHelp);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.domainAppRouting.recover })).toBeEnabled()
    );
    const saved = intent();
    view.unmount();
    allowed = false;
    render(<View />);
    await open();
    await screen.findByText(en.domainAppRouting.unknownHelp);
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.recover }));
    await screen.findByText("WORKFLOW_DISPATCH_UNCONFIRMED");
    expect(intent().input.requestId).toBe(saved.input.requestId);
    expect(writes()).toHaveLength(1);
    expect(
      requests.find((r) => r.operationName === "DomainAppRouteOperation")?.variables.input
    ).toEqual({ domainId: id(3), requestId: saved.input.requestId });
  });
  it("does not trust a true write envelope with another original request", async () => {
    render(<View />);
    await ready();
    wrong = "reply";
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
    await screen.findByText(en.domainAppRouting.unknownHelp);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.domainAppRouting.recover })).toBeEnabled()
    );
    expect(screen.queryByText("WORKFLOW_DISPATCH_UNCONFIRMED")).toBeNull();
    expect(writes()).toHaveLength(1);
    expect(intent().input.requestId).toBe(row!.requestId);
  });
  it("freshly reads original action/version before exact reconcile and preserves the original UUID", async () => {
    render(<View />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
    await screen.findByText("WORKFLOW_DISPATCH_UNCONFIRMED");
    const saved = intent();
    row = { ...row!, version: 5 };
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.reconcile }));
    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(writes()[1].variables.input).toEqual({
      operationId: id(8),
      expectedVersion: 5,
      requestId: intent().reconcile.requestId,
    });
    expect(intent().input.requestId).toBe(saved.input.requestId);
  });
  it("invalidates held review across actor A→B→A without adopting an old response", async () => {
    hold = true;
    const view = render(<View />);
    await choose();
    await waitFor(() => expect(release).not.toBeNull());
    view.rerender(<View ctx={{ ...context, actor: id(11) }} />);
    view.rerender(<View />);
    await act(async () => release?.());
    await open();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.next }));
    await screen.findByLabelText(en.domainAppRouting.targetStep);
    expect(screen.queryByText(en.domainAppRouting.targetPending)).toBeNull();
    expect(writes()).toHaveLength(0);
  });
  it("clears selected authority on paging and cannot reuse the old target review", async () => {
    render(<View />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.next }));
    await screen.findByLabelText(en.domainAppRouting.targetStep);
    fireEvent.change(screen.getByLabelText(en.domainAppRouting.targetStep), {
      target: { value: `${target.environmentId}:${target.workloadId}` },
    });
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.nextPage }));
    await waitFor(() =>
      expect(screen.getByLabelText(en.domainAppRouting.targetStep)).toHaveValue("")
    );
    expect(screen.getByRole("button", { name: en.domainAppRouting.review })).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });
  it("refuses write when durable intent storage fails", async () => {
    render(<View />);
    await ready();
    const savedStorage = sessionStorage;
    const refuse = vi.fn(() => {
      throw new Error("Unavailable");
    });
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => savedStorage.getItem(key),
      setItem: refuse,
    });
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
    await screen.findByText(en.domainAppRouting.storageUnavailable);
    expect(writes()).toHaveLength(0);
    expect(refuse).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });
  it("single-flights double Connect and rejects a recovered tuple substituted after a lost reply", async () => {
    lost = true;
    render(<View />);
    await ready();
    const button = screen.getByRole("button", { name: en.domainAppRouting.attach });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(writes()).toHaveLength(1));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.domainAppRouting.recover })).toBeEnabled()
    );
    row = { ...row!, workloadId: id(99) };
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.recover }));
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "DomainAppRouteOperation")).toHaveLength(1)
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.domainAppRouting.recover })).toBeEnabled()
    );
    expect(screen.queryByText(en.domainAppRouting.recordedHelp)).toBeNull();
    expect(writes()).toHaveLength(1);
  });
  it("does not reconcile when the fresh original-operation check withdraws its capability", async () => {
    render(<View />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
    await screen.findByText("WORKFLOW_DISPATCH_UNCONFIRMED");
    row = { ...row!, canReconcile: false };
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.reconcile }));
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "DomainAppRouteOperation")).toHaveLength(1)
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.domainAppRouting.recover })).toBeEnabled()
    );
    expect(writes()).toHaveLength(1);
  });
  it("keeps a completed preparation separate from Connect and requires a new review", async () => {
    review = {
      ...review,
      certificateStatus: "PENDING_VALIDATION",
      canPrepare: true,
      canAttach: false,
    };
    render(<View />);
    await choose();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.domainAppRouting.prepare })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.prepare }));
    await screen.findByText("WORKFLOW_DISPATCH_UNCONFIRMED");
    const saved = intent();
    row = { ...row!, status: "ready" };
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.recover }));
    await screen.findByRole("button", { name: en.domainAppRouting.continueAfterPrepare });
    review = { ...review, certificateStatus: "ISSUED", canPrepare: false, canAttach: true };
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.continueAfterPrepare }));
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    await waitFor(() => expect(sessionStorage.length).toBe(0));
    expect(screen.getByRole("button", { name: en.domainAppRouting.attach })).toBeDisabled();
    // Back stays disabled until the post-preparation refresh settles; a click
    // before then is ignored (slow CI runners hit this window).
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.managedDomains.back })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: en.managedDomains.back }));
    await screen.findByLabelText(en.domainAppRouting.targetStep);
    fireEvent.change(screen.getByLabelText(en.domainAppRouting.targetStep), {
      target: { value: `${target.environmentId}:${target.workloadId}` },
    });
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.review }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.domainAppRouting.attach })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.attach }));
    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(intent().input.requestId).not.toBe(saved.input.requestId);
    expect(writes().map((r) => r.operationName)).toEqual([
      "PrepareDomainAppRoute",
      "ConnectDomainAppRoute",
    ]);
  });
  it("does not retain old target authority after a current-support refresh refuses", async () => {
    render(<View />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: en.domainAppRouting.next }));
    await screen.findByLabelText(en.domainAppRouting.targetStep);
    const count = requests.filter((r) => r.operationName === "DomainAppRouteTargets").length;
    allowed = false;
    fireEvent.click(screen.getByRole("button", { name: en.managedDomains.refresh }));
    await screen.findByText("CURRENT_ROUTE_AUTHORITY_UNAVAILABLE");
    expect(requests.filter((r) => r.operationName === "DomainAppRouteTargets")).toHaveLength(count);
    expect(writes()).toHaveLength(0);
  });
});
