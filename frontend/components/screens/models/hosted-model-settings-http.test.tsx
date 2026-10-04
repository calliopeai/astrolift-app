import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { buildSchema, parse, validate } from "graphql";
import { readFileSync } from "node:fs";
import { createServer, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { SharedModelManagementClient } from "./SharedModelManagementClient";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import type {
  ClusterModelFieldsFragment,
  UpdateClusterModelInput,
} from "@/graphql/__generated__/operations";
const identity = vi.hoisted(() => ({ org: "org", actor: "admin" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
type Request = {
  operationName: string;
  query: string;
  variables: { input?: UpdateClusterModelInput; page?: number };
  response: ServerResponse;
};
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
const model: ClusterModelFieldsFragment = {
  ...sharedModelDetailProps.model!,
  name: "Local engine",
  sourceKind: "local_artifact",
  modelRepo: "local-engine",
  localArtifactId: "artifact-guid",
  localArtifactVersion: 3,
  localManifestSha256: "b".repeat(64),
  revisionSha: null,
  computeMode: "cpu",
  ready: false,
  desiredResources: {
    cpuRequest: "2",
    memoryRequest: "8Gi",
    gpuCount: 0,
    cpuKvCacheGiB: 2,
    replicas: 1,
  },
};
const app = { id: "app-guid", version: 8, name: "Storefront", slug: "storefront" };
let server: Server, client: ApolloClient, requests: Request[], holdWrite: boolean, allowed: boolean;
function reply(request: Request, data: Record<string, unknown>) {
  request.response.writeHead(200, { "content-type": "application/json" });
  request.response.end(JSON.stringify({ data }));
}
function mutationReply(request: Request, changes: Record<string, unknown> = {}) {
  const input = request.variables.input!;
  reply(request, {
    updateClusterModel: {
      ok: true,
      errors: [],
      data: {
        ...model,
        name: input.name,
        sharingMode: input.sharingMode,
        dedicatedAppId: input.dedicatedAppId,
        dedicatedAppVersion: input.ifMatchDedicatedAppVersion,
        dedicatedAppName: input.dedicatedAppId ? app.name : null,
        dedicatedAppSlug: input.dedicatedAppId ? app.slug : null,
        version: 6,
        status: "updating",
        ready: false,
        operationId: "reconcile-6",
        operationStartedAt: "2026-10-03T12:00:00Z",
        operationCompletedAt: null,
        desiredSubscriptionRevision: 4,
        subscriptionsEnabled: input.allowSubscriptions,
        desiredResources: {
          ...model.desiredResources,
          cpuRequest: input.cpuRequest,
          memoryRequest: input.memoryRequest,
          gpuCount: input.gpuCount,
          cpuKvCacheGiB: input.cpuKvCacheGiB,
        },
        ...changes,
      },
    },
  });
}
beforeEach(async () => {
  identity.org = "org";
  identity.actor = "admin";
  requests = [];
  holdWrite = false;
  allowed = true;
  server = createServer((incoming, response) => {
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.on("end", () => {
      const request = { ...JSON.parse(body), response } as Request;
      expect(validate(schema, parse(request.query))).toEqual([]);
      requests.push(request);
      if (request.operationName === "GetModelHostingAction")
        reply(request, { modelHostingAction: { allowed, reason: null } });
      else if (request.operationName === "GetClusterModelUpdateAdmission")
        reply(request, {
          clusterModelUpdateAdmission: {
            eligible: true,
            reason: null,
            runtimeVersion: "0.15.1",
            architecture: "amd64",
            hardwareAdmission: "operator_declared",
          },
        });
      else if (request.operationName === "ListModelDedicatedApps")
        reply(request, {
          clusterModelDedicatedAppsPage: {
            items: [app],
            totalCount: 26,
            page: request.variables.page,
            pageSize: 25,
            nextCursor: null,
          },
        });
      else if (request.operationName === "UpdateClusterModel") {
        if (!holdWrite) mutationReply(request);
      } else throw new Error(`Unexpected operation ${request.operationName}`);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({ uri: `http://127.0.0.1:${address.port}/gql` }),
  });
});
afterEach(async () => {
  client.stop();
  for (const request of requests) if (!request.response.writableEnded) request.response.end();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});
function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
}
function view(onRefresh = vi.fn(), current = model) {
  return <SharedModelManagementClient model={current} blocked={false} onRefresh={onRefresh} />;
}
async function review() {
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
    ).toBeEnabled()
  );
  fireEvent.click(screen.getByRole("button", { name: en.models.shared.management.reviewUpdate }));
}
function confirm() {
  fireEvent.click(screen.getByRole("button", { name: en.models.shared.management.confirmUpdate }));
}
describe("actual hosted model settings HTTP", () => {
  it("edits a local source without sending any provisioning source or secret and preserves pending readiness", async () => {
    const refresh = vi.fn();
    render(view(refresh), { wrapper });
    await waitFor(() =>
      expect(screen.getByLabelText(en.models.shared.placement.name)).toBeEnabled()
    );
    fireEvent.change(screen.getByLabelText(en.models.shared.placement.name), {
      target: { value: "Renamed engine" },
    });
    fireEvent.change(screen.getByLabelText(en.models.shared.placement.cpuRequest), {
      target: { value: "4" },
    });
    await review();
    confirm();
    await screen.findByText(en.models.shared.management.acceptedUpdate);
    const write = requests.find((r) => r.operationName === "UpdateClusterModel")!;
    expect(write.variables.input).toMatchObject({
      name: "Renamed engine",
      cpuRequest: "4",
      cpuKvCacheGiB: 2,
      gpuCount: 0,
      sharingMode: "SHARED",
      id: model.id,
      ifMatchVersion: 5,
    });
    for (const key of [
      "modelRepo",
      "revisionSha",
      "localArtifactId",
      "connectionId",
      "token",
      "url",
    ])
      expect(write.variables.input).not.toHaveProperty(key);
    expect(refresh).toHaveBeenCalledOnce();
    expect(screen.queryByText(en.models.shared.management.completedUpdate)).not.toBeInTheDocument();
    expect(client.cache.extract()).toEqual({});
  });
  it("selects a versioned app from the actual paged chooser for dedicated access", async () => {
    render(view(), { wrapper });
    await waitFor(() =>
      expect(screen.getByLabelText(en.models.shared.inventory.dedicated)).toBeEnabled()
    );
    fireEvent.click(screen.getByLabelText(en.models.shared.inventory.dedicated));
    await screen.findByRole("button", { name: "Storefront · storefront" });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(
        requests.filter((r) => r.operationName === "ListModelDedicatedApps").at(-1)?.variables.page
      ).toBe(2)
    );
    fireEvent.click(screen.getByRole("button", { name: "Storefront · storefront" }));
    await review();
    confirm();
    await screen.findByText(en.models.shared.management.acceptedUpdate);
    expect(
      requests.find((r) => r.operationName === "UpdateClusterModel")?.variables.input
    ).toMatchObject({
      sharingMode: "DEDICATED",
      dedicatedAppId: app.id,
      ifMatchDedicatedAppVersion: 8,
    });
  });
  it("clears the previously dedicated app when reviewing shared access", async () => {
    const current = {
      ...model,
      sharingMode: "DEDICATED" as const,
      dedicatedAppId: app.id,
      dedicatedAppVersion: app.version,
      dedicatedAppName: app.name,
      dedicatedAppSlug: app.slug,
    };
    render(view(vi.fn(), current), { wrapper });
    await waitFor(() =>
      expect(screen.getByLabelText(en.models.shared.inventory.shared)).toBeEnabled()
    );
    fireEvent.click(screen.getByLabelText(en.models.shared.inventory.shared));
    await review();
    confirm();
    await screen.findByText(en.models.shared.management.acceptedUpdate);
    expect(
      requests.find((request) => request.operationName === "UpdateClusterModel")?.variables.input
    ).toMatchObject({
      sharingMode: "SHARED",
      dedicatedAppId: null,
      ifMatchDedicatedAppVersion: null,
    });
  });
  it("refuses an accepted-looking response with a different dedicated app version", async () => {
    holdWrite = true;
    render(view(), { wrapper });
    await waitFor(() =>
      expect(screen.getByLabelText(en.models.shared.inventory.dedicated)).toBeEnabled()
    );
    fireEvent.click(screen.getByLabelText(en.models.shared.inventory.dedicated));
    fireEvent.click(await screen.findByRole("button", { name: "Storefront · storefront" }));
    await review();
    confirm();
    await waitFor(() =>
      expect(requests.some((r) => r.operationName === "UpdateClusterModel")).toBe(true)
    );
    await act(async () =>
      mutationReply(requests.find((r) => r.operationName === "UpdateClusterModel")!, {
        dedicatedAppVersion: 9,
      })
    );
    await screen.findByText(en.models.shared.management.failed);
    expect(screen.queryByText(en.models.shared.management.acceptedUpdate)).not.toBeInTheDocument();
  });
  it("ignores a held mutation after actor ABA and never refreshes the replacement context", async () => {
    holdWrite = true;
    const refresh = vi.fn();
    const frame = render(view(refresh), { wrapper });
    await review();
    confirm();
    await waitFor(() =>
      expect(requests.some((r) => r.operationName === "UpdateClusterModel")).toBe(true)
    );
    const held = requests.find((r) => r.operationName === "UpdateClusterModel")!;
    identity.actor = "other-admin";
    frame.rerender(view(refresh));
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "GetModelHostingAction")).toHaveLength(2)
    );
    identity.actor = "admin";
    frame.rerender(view(refresh));
    await waitFor(() =>
      expect(requests.filter((r) => r.operationName === "GetModelHostingAction")).toHaveLength(3)
    );
    await act(async () => mutationReply(held));
    expect(screen.queryByText(en.models.shared.management.acceptedUpdate)).not.toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
  });
  it("keeps all settings disabled when the actual hosting-admin action is refused", async () => {
    allowed = false;
    render(view(), { wrapper });
    await screen.findByText(en.models.shared.management.readOnly);
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
    ).toBeDisabled();
    expect(requests.map((r) => r.operationName)).toEqual(["GetModelHostingAction"]);
  });
});
