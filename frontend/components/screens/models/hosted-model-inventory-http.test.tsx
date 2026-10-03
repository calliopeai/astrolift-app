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
import { ModelsClient } from "@/app/(app)/models/models-client";
import { SharedModelClient } from "@/app/(app)/models/shared/[id]/shared-model-client";
import { sharedModelManagementProps } from "./shared-model-management.fixtures";

const scope = vi.hoisted(() => ({ org: "org-one", actor: "actor-one" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: scope.org }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({ useMe: () => ({ user: { id: scope.actor } }) }));
vi.mock("@/components/list/use-list-state", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/list/use-list-state")>();
  return { ...actual, useListState: actual.useLocalListState };
});
// Children have independent scoped read proofs; this fixture exercises actual list/detail route reads.
vi.mock("./ModelSubscriptionsClient", () => ({ ModelSubscriptionsClient: () => null }));
vi.mock("./ModelObservationsClient", () => ({ ModelObservationsClient: () => null }));
vi.mock("./SharedModelPromptClient", () => ({ SharedModelPromptClient: () => null }));
vi.mock("./SharedModelManagementClient", () => ({ SharedModelManagementClient: () => null }));
type Request = {
  operationName: string;
  query: string;
  variables: Record<string, unknown>;
  actor: string;
  response: ServerResponse;
};
let server: Server, client: ApolloClient, requests: Request[];
let handler: (request: Request) => void;
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
function model(organizationId: string, name: string, id = "local-model") {
  return {
    ...sharedModelManagementProps.model,
    __typename: "ClusterModelDeployment",
    organizationId,
    id,
    name,
    sourceKind: "local_artifact",
    localArtifactId: "artifact-one",
    localArtifactVersion: 3,
    localManifestSha256: "a".repeat(64),
    revisionSha: null,
    computeMode: "cpu",
    ready: false,
    readinessObservedAt: null,
    readinessGeneration: null,
    desiredResources: {
      cpuRequest: "4",
      memoryRequest: "8Gi",
      gpuCount: 0,
      cpuKvCacheGiB: 2,
      replicas: 1,
    },
    appliedResources: null,
  };
}
function reply(request: Request, name: string) {
  const row = model(
    String(request.variables.organizationId),
    name,
    String(request.variables.id ?? "local-model")
  );
  const data =
    request.operationName === "ListClusterModelsPage"
      ? {
          clusterModelDeploymentsPage: {
            items: [row],
            totalCount: 26,
            page: request.variables.page,
            pageSize: request.variables.pageSize,
            nextCursor: null,
          },
        }
      : { clusterModelDeployment: row };
  request.response.writeHead(200, { "content-type": "application/json" });
  request.response.end(JSON.stringify({ data }));
}
function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
}
beforeEach(async () => {
  scope.org = "org-one";
  scope.actor = "actor-one";
  requests = [];
  handler = (request) => reply(request, "Local model");
  server = createServer((incoming, response) => {
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.on("end", () => {
      const request = {
        ...JSON.parse(body),
        actor: String(incoming.headers["x-test-actor"]),
        response,
      } as Request;
      expect(validate(schema, parse(request.query))).toEqual([]);
      requests.push(request);
      handler(request);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: `http://127.0.0.1:${address.port}/gql`,
      fetch: (url, options) =>
        fetch(url, { ...options, headers: { ...options?.headers, "x-test-actor": scope.actor } }),
    }),
  });
});
afterEach(async () => {
  client.stop();
  for (const request of requests) if (!request.response.writableEnded) request.response.end();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});
describe("hosted model routes over real owned HTTP", () => {
  it("shows immutable local source and requested CPU resources, pages, and links exact deployment", async () => {
    render(<ModelsClient />, { wrapper });
    await screen.findByText("Local model");
    expect(screen.getByText("Local files")).toBeInTheDocument();
    expect(screen.getByText("4 CPU · 8Gi memory · 0 GPUs")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Local model/ })).toHaveAttribute(
      "href",
      "/models/shared/local-model"
    );
    expect(screen.queryByText("Last reconciliation confirmed")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(requests.at(-1)?.variables.page).toBe(2));
    expect(client.cache.extract()).toEqual({});
  });
  it.each(["actor", "org"] as const)(
    "does not restore an older held inventory response after %s ABA",
    async (binding) => {
      handler = () => {};
      const view = render(<ModelsClient />, { wrapper });
      await waitFor(() => expect(requests).toHaveLength(1));
      const first = requests[0];
      if (binding === "actor") scope.actor = "actor-two";
      else scope.org = "org-two";
      view.rerender(<ModelsClient />);
      await waitFor(() => expect(requests).toHaveLength(2));
      if (binding === "actor") scope.actor = "actor-one";
      else scope.org = "org-one";
      view.rerender(<ModelsClient />);
      await waitFor(() => expect(requests).toHaveLength(3));
      await act(async () => {
        reply(requests[2], "Current model");
      });
      await screen.findByText("Current model");
      await act(async () => {
        reply(first, "Withdrawn model");
        reply(requests[1], "Other context model");
      });
      expect(screen.queryByText("Withdrawn model")).not.toBeInTheDocument();
      expect(screen.queryByText("Other context model")).not.toBeInTheDocument();
      expect(screen.getByText("Current model")).toBeInTheDocument();
      expect(requests.map((request) => request.actor)).toEqual(
        binding === "actor"
          ? ["actor-one", "actor-two", "actor-one"]
          : ["actor-one", "actor-one", "actor-one"]
      );
      expect(client.cache.extract()).toEqual({});
    }
  );
  it("reads the exact selected local deployment and refuses a late prior actor detail", async () => {
    handler = () => {};
    const view = render(<SharedModelClient id="selected-model" />, { wrapper });
    await waitFor(() => expect(requests).toHaveLength(1));
    const first = requests[0];
    expect(first.variables).toEqual({ organizationId: "org-one", id: "selected-model" });
    scope.actor = "actor-two";
    view.rerender(<SharedModelClient id="selected-model" />);
    await waitFor(() => expect(requests).toHaveLength(2));
    await act(async () => {
      reply(requests[1], "Current local detail");
    });
    await screen.findByRole("heading", { name: "Current local detail" });
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    expect(screen.queryByText(/confirmed at/)).not.toBeInTheDocument();
    await act(async () => {
      reply(first, "Withdrawn detail");
    });
    expect(screen.queryByText("Withdrawn detail")).not.toBeInTheDocument();
    expect(client.cache.extract()).toEqual({});
  });
});
