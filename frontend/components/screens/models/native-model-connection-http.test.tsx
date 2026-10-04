import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { buildSchema, graphql } from "graphql";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import type { UpdateBedrockModelConnectionInput } from "@/graphql/__generated__/operations";
import { ModelsClient } from "@/app/(app)/models/models-client";
import { SharedModelClient } from "@/app/(app)/models/shared/[id]/shared-model-client";
import { NativeModelSettingsClient } from "./NativeModelSettingsClient";
import { ModelAddChoiceClient } from "./ModelAddChoiceClient";
import { NativeModelConnectClient } from "./NativeModelConnectClient";
import { nativeModel, nativeSource, projectedNativeModel } from "./native-model.fixtures";

const identity = vi.hoisted(() => ({ org: "", actor: "" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
vi.mock("@/components/list/use-list-state", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/list/use-list-state")>();
  return { ...actual, useListState: actual.useLocalListState };
});
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
  gone: boolean,
  enabled: boolean,
  admitted: boolean,
  malformedSupport: boolean,
  source: typeof nativeSource,
  unknownReply: boolean,
  replyForeign: boolean,
  currentModel: typeof nativeModel,
  hold: string | null,
  released: (() => void) | null;
beforeEach(async () => {
  identity.org = nativeModel.organizationId;
  identity.actor = "019e1abc-0000-7000-8000-000000000009";
  currentModel = structuredClone(nativeModel);
  requests = [];
  failures = [];
  gone = false;
  enabled = true;
  malformedSupport = false;
  admitted = true;
  source = structuredClone(nativeSource);
  unknownReply = false;
  replyForeign = false;
  hold = null;
  released = null;
  const roots = {
    astroliftServerInfo: () => ({ capabilities: [] }),
    clusterModelDeploymentsPage: ({ page, pageSize }: { page: number; pageSize: number }) => ({
      page,
      pageSize,
      totalCount: 1,
      nextCursor: null,
      items: [currentModel],
    }),
    clusterModelSubscriptionsPage: ({ page, pageSize }: { page: number; pageSize: number }) => ({
      page,
      pageSize,
      totalCount: 0,
      nextCursor: null,
      items: [],
    }),
    modelHostingAction: () => ({
      allowed: enabled,
      reason: enabled ? null : "Hosting authority unavailable",
    }),
    clusterModelDeployment: () => (gone ? null : currentModel),
    updateBedrockModelConnection: ({ input }: { input: UpdateBedrockModelConnectionInput }) =>
      unknownReply
        ? null
        : {
            ok: true,
            errors: [],
            data: (currentModel = {
              ...currentModel,
              name: input.name,
              version: currentModel.version + 1,
              status: "updating",
              subscriptionsEnabled: input.allowSubscriptions,
              sharingMode: input.sharingMode ?? currentModel.sharingMode,
              dedicatedAppId: input.dedicatedAppId,
              dedicatedAppVersion: input.ifMatchDedicatedAppVersion,
              desiredSubscriptionRevision: currentModel.desiredSubscriptionRevision + 1,
              operationId: "019e1abc-0000-7000-8000-000000000011",
              operationStartedAt: "2026-10-04T01:00:00Z",
              operationCompletedAt: null,
            }),
          },
    unregisterBedrockModelConnection: () => {
      if (unknownReply) return null;
      gone = true;
      return { ok: true, errors: [], data: currentModel };
    },
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
    if (
      malformedSupport &&
      ["GetModelHostingAction", "GetBedrockModelSupport"].includes(payload.operationName)
    ) {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ data: {} }));
      return;
    }
    const result = await graphql({
      schema,
      source: payload.query,
      variableValues: payload.variables,
      rootValue: roots,
    });
    for (const error of result.errors ?? []) {
      // The deliberately missing write reply is an uncertainty test, not schema acceptance.
      if (
        !unknownReply ||
        !["RegisterBedrockModel", "UpdateBedrockModel", "UnregisterBedrockModel"].includes(
          payload.operationName
        )
      )
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

const Settings = () => (
  <ApolloProvider client={client}>
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <NativeModelSettingsClient model={nativeModel} blocked={false} onRefresh={() => {}} />
    </NextIntlClientProvider>
  </ApolloProvider>
);
async function settingsReview(kind = "Review connection settings") {
  await waitFor(() => expect(screen.getByRole("button", { name: kind })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: kind }));
  return within(await screen.findByRole("alertdialog"));
}
describe("native local settings with fresh HTTP admission", () => {
  it("queues only local settings with a current immutable source/version read", async () => {
    render(<Settings />);
    await waitFor(() => expect(screen.getByLabelText("Connection name")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Connection name"), {
      target: { value: "Renamed native source" },
    });
    const dialog = await settingsReview();
    fireEvent.click(dialog.getByRole("button", { name: "Review connection settings" }));
    await screen.findByText(
      "Settings accepted for binding reconciliation. Cloud invocation is still unverified."
    );
    expect(
      requests.find((row) => row.operationName === "UpdateBedrockModel")?.variables.input
    ).toEqual({
      organizationId: nativeModel.organizationId,
      id: nativeModel.id,
      expectedClusterId: nativeModel.clusterId,
      expectedProviderId: nativeModel.providerId,
      ifMatchVersion: nativeModel.version,
      name: "Renamed native source",
      allowSubscriptions: true,
      sharingMode: "SHARED",
      dedicatedAppId: null,
      ifMatchDedicatedAppVersion: null,
    });
    expect(requests.map((row) => row.operationName)).toEqual([
      "GetBedrockModelSupport",
      "GetBedrockModelSupport",
      "GetClusterModelDeployment",
      "UpdateBedrockModel",
    ]);
    expect(client.cache.extract()).toEqual({});
  });
  it("recognizes synchronous local removal without pretending cloud deletion or a queued operation", async () => {
    render(<Settings />);
    const dialog = await settingsReview("Unregister connection");
    expect(dialog.getByText(/This never deletes the cloud model/)).toBeVisible();
    fireEvent.click(dialog.getByRole("button", { name: "Unregister connection" }));
    await screen.findByText("Local connection removed. The cloud source was not deleted.");
    expect(requests.filter((row) => row.operationName === "UnregisterBedrockModel")).toHaveLength(
      1
    );
  });
  it.each(["version", "source", "cluster"])(
    "refuses %s replacement before a local write",
    async (replacement) => {
      render(<Settings />);
      const dialog = await settingsReview();
      if (replacement === "version") currentModel.version += 1;
      if (replacement === "source") currentModel.nativeSource!.sourceFingerprint = "b".repeat(64);
      if (replacement === "cluster")
        currentModel.clusterId = "019e1abc-0000-7000-8000-000000000099";
      fireEvent.click(dialog.getByRole("button", { name: "Review connection settings" }));
      await screen.findByText(
        "The reviewed source or placement changed. Read it again before continuing."
      );
      expect(
        requests.some((row) => /UpdateBedrock|UnregisterBedrock/.test(row.operationName))
      ).toBe(false);
    }
  );
  it("honors newly withdrawn support before the model read and mutation", async () => {
    render(<Settings />);
    const dialog = await settingsReview();
    enabled = false;
    fireEvent.click(dialog.getByRole("button", { name: "Review connection settings" }));
    await screen.findByText("Operator disabled native registration");
    expect(requests.map((row) => row.operationName)).toEqual([
      "GetBedrockModelSupport",
      "GetBedrockModelSupport",
    ]);
  });
  it("blocks an unconfirmed local settings write instead of repeating it", async () => {
    render(<Settings />);
    const dialog = await settingsReview();
    unknownReply = true;
    fireEvent.click(dialog.getByRole("button", { name: "Review connection settings" }));
    await waitFor(() =>
      expect(screen.getAllByText(/The write outcome is unconfirmed/).length).toBeGreaterThan(0)
    );
    await waitFor(() =>
      expect(dialog.getByRole("button", { name: "Review connection settings" })).toBeDisabled()
    );
    expect(requests.filter((row) => row.operationName === "UpdateBedrockModel")).toHaveLength(1);
  });
  it.each(["actor", "org"] as const)(
    "does not write after held preflight %s ABA",
    async (field) => {
      const view = render(<Settings />);
      const dialog = await settingsReview();
      hold = "GetClusterModelDeployment";
      fireEvent.click(dialog.getByRole("button", { name: "Review connection settings" }));
      await waitFor(() => expect(released).not.toBeNull());
      const previous = identity[field];
      identity[field] = "019e1abc-0000-7000-8000-000000000099";
      view.rerender(<Settings />);
      identity[field] = previous;
      view.rerender(<Settings />);
      await act(async () => {
        released?.();
      });
      expect(requests.some((row) => row.operationName === "UpdateBedrockModel")).toBe(false);
      expect(screen.queryByRole("alertdialog")).toBeNull();
    }
  );
  it("entry choices use server support rather than plugin visibility", async () => {
    enabled = false;
    render(
      <ApolloProvider client={client}>
        <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
          <ModelAddChoiceClient />
        </NextIntlClientProvider>
      </ApolloProvider>
    );
    await screen.findByText("Operator disabled native registration");
    expect(screen.getByRole("button", { name: "Connect a cloud model" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "Connect a cloud model" })).toBeNull();
    expect(requests.some((row) => row.operationName === "ListNativeModelClusters")).toBe(false);
  });
});

it("partial support envelopes fail closed without rendering an executable choice", async () => {
  malformedSupport = true;
  const view = render(
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ModelAddChoiceClient />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  await waitFor(() => expect(requests).toHaveLength(2));
  await waitFor(() => expect(view.container.querySelectorAll("button:disabled")).toHaveLength(2));
  expect(screen.queryByRole("link")).toBeNull();
});

it("native detail never mounts hosted runtime, prompt, density or traffic transports", async () => {
  render(
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <SharedModelClient id={nativeModel.id} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  await screen.findByText(nativeModel.nativeSource!.sourceArn);
  await waitFor(() =>
    expect(requests.some((row) => row.operationName === "ListClusterModelSubscriptions")).toBe(true)
  );
  expect(screen.getByText(en.models.native.details.description)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Run model test" })).toBeNull();
  expect(
    requests.some((row) =>
      /Metrics|Density|RuntimeAdmission|Prompt|UpdateAdmission/.test(row.operationName)
    )
  ).toBe(false);
  expect(client.cache.extract()).toEqual({});
});
it("unknown native variants remain read-only without fallback transports or settings links", async () => {
  currentModel.sourceKind = "future_native";
  currentModel.nativeSource = null;
  render(
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <SharedModelClient id={nativeModel.id} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  await screen.findByText(en.models.native.details.unsupported);
  expect(requests.map((row) => row.operationName)).toEqual(["GetClusterModelDeployment"]);
  expect(document.querySelector('a[href="#model-settings"]')).toBeNull();
  expect(screen.queryByRole("button", { name: "Run model test" })).toBeNull();
});

it.each(["Native feature is disabled", "Provider declaration is unavailable"])(
  "known native detail preserves unavailable identity after %s",
  async (reason) => {
    enabled = false;
    currentModel = { ...projectedNativeModel("withdrawn"), reason };
    render(
      <ApolloProvider client={client}>
        <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
          <SharedModelClient id={nativeModel.id} />
        </NextIntlClientProvider>
      </ApolloProvider>
    );
    await screen.findAllByText(en.models.native.details.unavailable);
    await waitFor(() =>
      expect(requests.some((row) => row.operationName === "GetBedrockModelSupport")).toBe(true)
    );
    expect(screen.getAllByText(reason).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Review connection settings" })).toBeDisabled();
    expect(screen.getByRole("button", { name: en.models.shared.connections.add })).toBeDisabled();
    expect(screen.queryByText(nativeSource.identity.sourceArn)).toBeNull();
    expect(screen.queryByText(nativeSource.identity.accountId)).toBeNull();
    expect(screen.queryByText(en.models.shared.detail.resourcesHelp)).toBeNull();
    expect(screen.queryByRole("button", { name: "Run model test" })).toBeNull();
    expect(
      requests.some((row) =>
        /Metrics|Density|RuntimeAdmission|Prompt|UpdateAdmission|BedrockModelSource/.test(
          row.operationName
        )
      )
    ).toBe(false);
    expect(requests.some((row) => /UpdateBedrock|UnregisterBedrock/.test(row.operationName))).toBe(
      false
    );
  }
);
it("retains accepted local removal when the fresh exact detail is missing", async () => {
  render(
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <SharedModelClient id={nativeModel.id} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  await screen.findByText(nativeSource.identity.sourceArn);
  const dialog = await settingsReview("Unregister connection");
  fireEvent.click(dialog.getByRole("button", { name: "Unregister connection" }));
  await waitFor(() => expect(gone).toBe(true));
  await screen.findByText(en.models.native.details.removed);
  await waitFor(() =>
    expect(
      requests.filter((row) => row.operationName === "GetClusterModelDeployment")
    ).toHaveLength(3)
  );
  expect(screen.queryByText(nativeSource.identity.sourceArn)).toBeNull();
  expect(requests.filter((row) => row.operationName === "UnregisterBedrockModel")).toHaveLength(1);
});

it("retains accepted native settings across version refresh and drops receipt on actor ABA", async () => {
  const Wrapper = () => (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <SharedModelClient id={nativeModel.id} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  const view = render(<Wrapper />);
  await screen.findByText(nativeSource.identity.sourceArn);
  const dialog = await settingsReview();
  fireEvent.click(dialog.getByRole("button", { name: en.models.native.details.saveSettings }));
  await waitFor(() => expect(currentModel.version).toBe(nativeModel.version + 1));
  await waitFor(() =>
    expect(
      requests.filter((row) => row.operationName === "GetClusterModelDeployment")
    ).toHaveLength(3)
  );
  await screen.findByText(en.models.native.details.queued);
  expect(
    screen.getByRole("button", { name: en.models.native.details.saveSettings })
  ).toBeDisabled();
  const oldActor = identity.actor;
  identity.actor = "019e1abc-0000-7000-8000-000000000099";
  view.rerender(<Wrapper />);
  identity.actor = oldActor;
  view.rerender(<Wrapper />);
  await screen.findByText(nativeSource.identity.sourceArn);
  expect(screen.queryByText(en.models.native.details.queued)).toBeNull();
  expect(requests.filter((row) => row.operationName === "UpdateBedrockModel")).toHaveLength(1);
});

it.each(["Native feature is disabled", "Provider declaration is unavailable"])(
  "mounted inventory preserves unavailable native row after %s",
  async (reason) => {
    enabled = false;
    currentModel = { ...projectedNativeModel("withdrawn"), reason };
    render(
      <ApolloProvider client={client}>
        <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
          <ModelsClient />
        </NextIntlClientProvider>
      </ApolloProvider>
    );
    await screen.findByText(nativeModel.name);
    expect(screen.getAllByText(/Amazon Bedrock/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(en.models.native.details.unavailable).length).toBeGreaterThan(0);
    expect(screen.getAllByText(en.models.native.details.notApplicable).length).toBe(2);
    expect(screen.getByRole("button", { name: en.models.native.add.connectAction })).toBeDisabled();
    expect(screen.queryByText(en.models.native.details.unsupported)).toBeNull();
    expect(
      requests.find((row) => row.operationName === "ListClusterModelsPage")?.variables
        .organizationId
    ).toBe(nativeModel.organizationId);
    expect(
      requests.some((row) =>
        /Metrics|Density|RuntimeAdmission|Prompt|UpdateAdmission|BedrockModelSource/.test(
          row.operationName
        )
      )
    ).toBe(false);
  }
);

it.each(["vertex_unadopted", "foundry_unadopted", "unknown_family"])(
  "mounted common %s metadata keeps unavailable family without cross-family transport or private fallback",
  async (variant) => {
    currentModel = {
      ...projectedNativeModel(variant),
      modelRepo: "PRIVATE_MODEL_URL",
      revisionSha: "PRIVATE_REVISION",
    };
    const view = render(
      <ApolloProvider client={client}>
        <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
          <SharedModelClient id={nativeModel.id} />
        </NextIntlClientProvider>
      </ApolloProvider>
    );
    await screen.findByRole("region", { name: en.models.shared.inventory.settings });
    expect(
      screen.getAllByText(en.models.native.common[currentModel.nativeConnection!.family]).length
    ).toBeGreaterThan(0);
    expect(requests.map((row) => row.operationName)).toEqual(["GetClusterModelDeployment"]);
    expect(view.container.innerHTML).not.toContain("PRIVATE_");
    expect(view.container.querySelector("#model-settings button")).toBeNull();
    expect(screen.queryByRole("button", { name: "Run model test" })).toBeNull();
    expect(client.cache.extract()).toEqual({});
    expect(failures).toEqual([]);
  }
);
it("advertised but malformed common metadata blocks legacy Bedrock settings and all fallback transports", async () => {
  currentModel = { ...nativeModel, nativeConnection: null, modelRepo: "PRIVATE_MODEL_URL" };
  const view = render(
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <SharedModelClient id={nativeModel.id} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  await screen.findByText(en.models.native.details.unsupported);
  expect(requests.map((row) => row.operationName)).toEqual(["GetClusterModelDeployment"]);
  expect(view.container.innerHTML).not.toContain("PRIVATE_MODEL_URL");
  expect(view.container.querySelector('a[href="#model-settings"]')).toBeNull();
});

it("unknown Bedrock source remains unavailable and cannot enter hosted or native intake", async () => {
  currentModel = projectedNativeModel("unknown");
  render(
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <SharedModelClient id={nativeModel.id} />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  const add = await screen.findByRole("button", { name: en.models.shared.connections.add });
  expect(add).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Run model test" })).toBeNull();
  expect(
    requests.some((row) =>
      /ModelConnectionTargets|BedrockModelSource|RuntimeAdmission|Prompt/.test(row.operationName)
    )
  ).toBe(false);
});
