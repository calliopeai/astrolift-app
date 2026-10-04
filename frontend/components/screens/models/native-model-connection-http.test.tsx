import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { buildSchema, graphql } from "graphql";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { NativeModelConnectClient } from "./NativeModelConnectClient";
import { nativeModel, nativeSource } from "./native-model.fixtures";

const identity = vi.hoisted(() => ({ org: "", actor: "" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
const cluster = {
  id: nativeModel.clusterId,
  version: 3,
  providerId: nativeModel.providerId,
  providerVersion: 2,
  name: "Research",
  slug: "research",
  region: "us-east-1",
};
type Request = { operationName: string; variables: Record<string, unknown> };
let server: Server,
  client: ApolloClient,
  requests: Request[],
  failures: string[],
  enabled: boolean,
  admitted: boolean,
  source: typeof nativeSource,
  unknownReply: boolean,
  replyForeign: boolean,
  hold: string | null,
  released: (() => void) | null;
beforeEach(async () => {
  identity.org = nativeModel.organizationId;
  identity.actor = "019e1abc-0000-7000-8000-000000000009";
  requests = [];
  failures = [];
  enabled = true;
  admitted = true;
  source = structuredClone(nativeSource);
  unknownReply = false;
  replyForeign = false;
  hold = null;
  released = null;
  const roots = {
    bedrockModelConnectionSupport: () => ({
      enabled,
      allowed: enabled,
      reason: enabled ? null : "Operator disabled native registration",
    }),
    clusterModelPlacementClustersPage: ({
      page,
      pageSize,
    }: {
      page: number;
      pageSize: number;
    }) => ({ page, pageSize, totalCount: 1, items: [cluster] }),
    bedrockModelConnectionAction: () => ({
      enabled,
      allowed: admitted,
      reason: admitted ? null : "Placement version changed",
    }),
    bedrockModelSource: () => source,
    bedrockModelSources: () => ({
      items: [source],
      state: "metadata",
      reason: "Some source reads failed",
      partial: true,
      truncated: true,
    }),
    clusterModelDedicatedAppsPage: ({ page, pageSize }: { page: number; pageSize: number }) => ({
      page,
      pageSize,
      totalCount: 1,
      items: [
        {
          id: "019e1abc-0000-7000-8000-000000000008",
          version: 7,
          name: "Storefront",
          slug: "storefront",
        },
      ],
    }),
    registerBedrockModelConnection: ({ input }: { input: Record<string, unknown> }) =>
      unknownReply
        ? null
        : {
            ok: true,
            errors: [],
            data: {
              ...nativeModel,
              name: input.name,
              status: "active",
              organizationId: replyForeign
                ? "019e1abc-0000-7000-8000-000000000099"
                : input.organizationId,
              subscriptionsEnabled: input.allowSubscriptions,
              sharingMode: input.sharingMode,
              dedicatedAppId: input.dedicatedAppId,
              dedicatedAppVersion: input.ifMatchDedicatedAppVersion,
              nativeSource: source.identity,
            },
          },
  };
  server = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const payload = JSON.parse(body);
    requests.push(payload);
    if (payload.operationName === hold)
      await new Promise<void>((resolve) => {
        released = resolve;
      });
    const result = await graphql({
      schema,
      source: payload.query,
      variableValues: payload.variables,
      rootValue: roots,
    });
    for (const error of result.errors ?? []) {
      // The deliberately missing write reply is an uncertainty test, not schema acceptance.
      if (!unknownReply || payload.operationName !== "RegisterBedrockModel")
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
  released?.();
  cleanup();
  client.stop();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
  expect(failures).toEqual([]);
});
const View = () => (
  <ApolloProvider client={client}>
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <NativeModelConnectClient />
    </NextIntlClientProvider>
  </ApolloProvider>
);
async function placement() {
  fireEvent.click(await screen.findByRole("button", { name: "Research · research" }));
  await screen.findByLabelText("Source ID or ARN");
}
async function inspect() {
  fireEvent.change(screen.getByLabelText("Source ID or ARN"), {
    target: { value: source.identity.sourceArn },
  });
  fireEvent.click(screen.getByRole("button", { name: "Inspect source" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Review connection" })).toBeEnabled()
  );
}
async function review() {
  await placement();
  await inspect();
  fireEvent.click(screen.getByRole("button", { name: "Review connection" }));
  await screen.findByRole("button", { name: "Register connection" });
}
describe("native connection actual SDL over HTTP", () => {
  it("registers the exact reviewed source without allocating runtime or claiming inference", async () => {
    render(<View />);
    await review();
    fireEvent.click(screen.getByRole("button", { name: "Register connection" }));
    await screen.findByText("Connection registered. Invoke access remains unverified.");
    expect(screen.getByRole("link", { name: "Open deployment" })).toHaveAttribute(
      "href",
      `/models/shared/${nativeModel.id}`
    );
    const writes = requests.filter((row) => row.operationName === "RegisterBedrockModel");
    expect(writes).toHaveLength(1);
    expect(writes[0].variables.input).toEqual({
      organizationId: nativeModel.organizationId,
      clusterId: cluster.id,
      expectedProviderId: cluster.providerId,
      expectedClusterVersion: 3,
      expectedProviderVersion: 2,
      sourceKind: "FOUNDATION_MODEL",
      sourceIdentifier: source.identity.sourceId,
      sourceFingerprint: source.identity.sourceFingerprint,
      name: source.name,
      allowSubscriptions: true,
      sharingMode: "SHARED",
      dedicatedAppId: null,
      ifMatchDedicatedAppVersion: null,
    });
    expect(requests.filter((row) => row.operationName === "GetBedrockModelSource")).toHaveLength(3);
    expect(requests.every((row) => !/Provision|Prompt|Metrics/.test(row.operationName))).toBe(true);
    expect(client.cache.extract()).toEqual({});
  });
  it("freezes the selected application's current GUID and version for dedication", async () => {
    render(<View />);
    await placement();
    await inspect();
    fireEvent.click(screen.getByRole("button", { name: "Dedicated" }));
    fireEvent.click(await screen.findByRole("button", { name: "Storefront" }));
    fireEvent.click(screen.getByRole("button", { name: "Review connection" }));
    fireEvent.click(await screen.findByRole("button", { name: "Register connection" }));
    await screen.findByText("Connection registered. Invoke access remains unverified.");
    expect(
      requests.find((row) => row.operationName === "RegisterBedrockModel")!.variables
        .input as Record<string, unknown>
    ).toMatchObject({
      sharingMode: "DEDICATED",
      dedicatedAppId: "019e1abc-0000-7000-8000-000000000008",
      ifMatchDedicatedAppVersion: 7,
    });
  });
  it("keeps bounded partial/truncated discovery honest and requires exact detail", async () => {
    render(<View />);
    await placement();
    fireEvent.click(screen.getByRole("button", { name: "Load sources" }));
    await screen.findByText("Some metadata reads failed; this list is incomplete.");
    expect(
      screen.getByText(
        "The result limit was reached. Use an exact ID or ARN to inspect another source."
      )
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: source.name }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Review connection" })).toBeEnabled()
    );
    expect(requests.some((row) => row.operationName === "GetBedrockModelSource")).toBe(true);
    expect(requests.some((row) => row.operationName === "RegisterBedrockModel")).toBe(false);
  });
  it("does not expose placement or perform provider reads when globally disabled", async () => {
    enabled = false;
    render(<View />);
    await screen.findByText("Operator disabled native registration");
    expect(requests.map((row) => row.operationName)).toEqual(["GetBedrockModelSupport"]);
    expect(screen.queryByLabelText("Source ID or ARN")).toBeNull();
  });
  it("refuses a source fingerprint change between review and submission", async () => {
    render(<View />);
    await review();
    source.identity.sourceFingerprint = "b".repeat(64);
    fireEvent.click(screen.getByRole("button", { name: "Register connection" }));
    await screen.findByText(
      "The reviewed source or placement changed. Read it again before continuing."
    );
    expect(requests.some((row) => row.operationName === "RegisterBedrockModel")).toBe(false);
  });
  it("preserves the server's current-placement refusal before registration", async () => {
    render(<View />);
    await review();
    admitted = false;
    fireEvent.click(screen.getByRole("button", { name: "Register connection" }));
    await screen.findByText("Placement version changed");
    expect(requests.some((row) => row.operationName === "RegisterBedrockModel")).toBe(false);
  });
  it.each(["missing", "foreign"])("blocks duplicate writes after a %s outcome", async (mode) => {
    render(<View />);
    await review();
    unknownReply = mode === "missing";
    replyForeign = mode === "foreign";
    fireEvent.click(screen.getByRole("button", { name: "Register connection" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Register connection" })).toBeDisabled()
    );
    await waitFor(() =>
      expect(screen.getAllByText(/The write outcome is unconfirmed/).length).toBeGreaterThan(0)
    );
    fireEvent.click(screen.getByRole("button", { name: "Register connection" }));
    expect(requests.filter((row) => row.operationName === "RegisterBedrockModel")).toHaveLength(1);
    expect(screen.queryByRole("link", { name: "Open deployment" })).toBeNull();
  });
  it.each(["actor", "org"] as const)("discards a held source read after %s ABA", async (field) => {
    const view = render(<View />);
    await placement();
    hold = "GetBedrockModelSource";
    fireEvent.change(screen.getByLabelText("Source ID or ARN"), {
      target: { value: source.identity.sourceId },
    });
    fireEvent.click(screen.getByRole("button", { name: "Inspect source" }));
    await waitFor(() => expect(released).not.toBeNull());
    const previous = identity[field];
    identity[field] = "019e1abc-0000-7000-8000-000000000099";
    view.rerender(<View />);
    await screen.findByRole("button", { name: "Research · research" });
    identity[field] = previous;
    view.rerender(<View />);
    await screen.findByRole("button", { name: "Research · research" });
    await act(async () => {
      released?.();
    });
    expect(screen.queryByLabelText("Connection name")).toBeNull();
    expect(requests.some((row) => row.operationName === "RegisterBedrockModel")).toBe(false);
  });
});
