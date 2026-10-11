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
import { ModelSubscriptionUsageClient } from "./ModelSubscriptionUsageClient";
import { ModelSubscriptionsClient } from "./ModelSubscriptionsClient";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import type { ModelSubscription } from "./ModelSubscriptionsPanel";
import type { ModelObservationState } from "@/graphql/__generated__/schema";
const identity = vi.hoisted(() => ({ org: "org", actor: "owner" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
const model = sharedModelDetailProps.model!;
const subscription: ModelSubscription = {
  id: "subscription-one",
  version: 2,
  alias: "chat",
  bindingPrefix: "MODEL_CHAT_",
  appSlug: "storefront",
  environmentName: "production",
  status: "active",
  desiredRevision: 3,
  appliedRevision: 3,
  reason: null,
  canRevoke: true,
};
type Request = {
  operationName: string;
  variables: Record<string, string | number>;
  response: ServerResponse;
};
let server: Server,
  client: ApolloClient,
  requests: Request[],
  held: boolean,
  state: ModelObservationState;
function reply(request: Request, payload: Record<string, unknown>) {
  request.response.writeHead(200, { "content-type": "application/json" });
  request.response.end(JSON.stringify(payload));
}
function metricReply(request: Request, changes: Record<string, unknown> = {}) {
  reply(request, {
    data: {
      astroliftModelSubscriptionMetrics: {
        serviceId: request.variables.serviceId,
        clusterId: request.variables.expectedClusterId,
        subscriptionId: request.variables.subscriptionId,
        start: request.variables.start,
        end: request.variables.end,
        retrievedAt: request.variables.end,
        stepSeconds: 30,
        scope: "authenticated_subscription",
        metrics: [
          ["requests_per_second", "requests/s", 17],
          ["error_requests_per_second", "requests/s", 0],
          ["response_bytes_per_second", "bytes/s", 1024],
          ["latency_p95", "seconds", 0.5],
        ].map(([key, unit, value]) => ({
          key,
          unit,
          value: state === "AVAILABLE" ? value : null,
          state,
          source: "authenticated_model_subscription",
          observedAt: state === "AVAILABLE" ? request.variables.end : null,
          aggregationWindowSeconds: 300,
          samples: [],
        })),
        ...changes,
      },
    },
  });
}
beforeEach(async () => {
  identity.org = "org";
  identity.actor = "owner";
  requests = [];
  held = false;
  state = "AVAILABLE";
  server = createServer((incoming, response) => {
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.on("end", () => {
      const request = { ...JSON.parse(body), response } as Request;
      expect(validate(schema, parse(JSON.parse(body).query))).toEqual([]);
      requests.push(request);
      if (request.operationName === "GetModelSubscriptionMetrics") {
        if (!held) metricReply(request);
      } else if (request.operationName === "ListClusterModelSubscriptions")
        reply(request, {
          data: {
            clusterModelSubscriptionsPage: {
              items: [
                subscription,
                { ...subscription, id: "subscription-two", appSlug: "checkout" },
              ].map((row) => ({
                ...row,
                modelDeploymentId: model.id,
                appId: row.id + "-app",
                appName: row.appSlug,
                environmentId: row.id + "-env",
                desiredEnabled: true,
                reconcileStartedAt: null,
                reconciledAt: null,
              })),
              totalCount: 21,
              nextCursor: null,
              page: request.variables.page,
              pageSize: request.variables.pageSize,
            },
          },
        });
      else if (request.operationName === "ListModelSubscriptionTargets")
        reply(request, {
          data: {
            clusterModelSubscriptionTargetsPage: {
              items: [],
              totalCount: 0,
              nextCursor: null,
              page: request.variables.page,
              pageSize: request.variables.pageSize,
            },
          },
        });
      else throw Error(`Unexpected operation ${request.operationName}`);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({ uri: `http://127.0.0.1:${(server.address() as AddressInfo).port}/gql` }),
  });
});
afterEach(async () => {
  client.stop();
  for (const r of requests) if (!r.response.writableEnded) r.response.end();
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
function view(current = model, sub = subscription) {
  return <ModelSubscriptionUsageClient model={current} subscription={sub} onClose={() => {}} />;
}
const metrics = () => requests.filter((r) => r.operationName === "GetModelSubscriptionMetrics");
describe("authenticated subscription traffic over actual HttpLink", () => {
  it("reads only the explicitly selected row and tears down on close without filling Apollo cache", async () => {
    render(
      <ModelSubscriptionsClient model={model} blocked={false} onRefreshDeployment={() => {}} />,
      { wrapper }
    );
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: en.models.shared.subscriptionUsage.view })
      ).toHaveLength(2)
    );
    expect(metrics()).toHaveLength(0);
    fireEvent.click(
      screen.getAllByRole("button", { name: en.models.shared.subscriptionUsage.view })[0]
    );
    await screen.findByText("17");
    expect(metrics()).toHaveLength(1);
    expect(metrics()[0].variables).toMatchObject({
      organizationId: model.organizationId,
      serviceId: model.id,
      subscriptionId: subscription.id,
      expectedClusterId: model.clusterId,
      expectedProviderId: model.providerId,
    });
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText(en.models.shared.subscriptionUsage.bytes)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.subscriptionUsage.dismiss })
    );
    expect(screen.queryByText("17")).not.toBeInTheDocument();
    expect(metrics()).toHaveLength(1);
    expect(JSON.stringify(client.cache.extract())).not.toContain("authenticated_subscription");
  });
  it.each(["UNCONFIGURED", "NO_DATA"] as const)(
    "keeps %s distinct from an observed zero",
    async (next) => {
      state = next;
      render(view(), { wrapper });
      await screen.findByText(
        en.models.shared.subscriptionUsage[next === "NO_DATA" ? "empty" : "unconfigured"]
      );
      expect(screen.queryByText("0")).not.toBeInTheDocument();
    }
  );
  it.each(["actor", "org"] as const)(
    "drops a held read through %s ABA and starts a fresh exact receipt",
    async (key) => {
      held = true;
      const v = render(view(), { wrapper });
      await waitFor(() => expect(metrics()).toHaveLength(1));
      const old = metrics()[0];
      identity[key] = "other";
      v.rerender(view());
      if (key === "actor") await waitFor(() => expect(metrics()).toHaveLength(2));
      identity[key] = key === "org" ? "org" : "owner";
      v.rerender(view());
      await waitFor(() => expect(metrics()).toHaveLength(key === "actor" ? 3 : 2));
      await act(async () => {
        metricReply(old);
      });
      expect(screen.queryByText("17")).not.toBeInTheDocument();
      await act(async () => {
        metricReply(metrics().at(-1)!);
      });
      await screen.findByText("17");
    }
  );
  it.each(["providerId", "clusterId", "id"] as const)(
    "does not release an old receipt after model %s changes",
    async (key) => {
      held = true;
      const v = render(view(), { wrapper });
      await waitFor(() => expect(metrics()).toHaveLength(1));
      const old = metrics()[0];
      v.rerender(view({ ...model, [key]: "replacement" }));
      await waitFor(() => expect(metrics()).toHaveLength(2));
      await act(async () => metricReply(old));
      expect(screen.queryByText("17")).not.toBeInTheDocument();
    }
  );
  it("refuses a mismatched subscription receipt and retains safe server denial rather than showing totals", async () => {
    held = true;
    render(view(), { wrapper });
    await waitFor(() => expect(metrics()).toHaveLength(1));
    await act(async () =>
      metricReply(metrics()[0], {
        subscriptionId: "foreign",
        scope: "deployment_aggregate_not_app_attributed",
      })
    );
    await screen.findAllByText(en.models.shared.subscriptionUsage.unavailable);
    expect(screen.queryByText("17")).not.toBeInTheDocument();
  });
  it("refreshes exactly once and labels retained same-subscription evidence stale after a denied read", async () => {
    render(view(), { wrapper });
    await screen.findByText("17");
    held = true;
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.observations.refresh }));
    await waitFor(() => expect(metrics()).toHaveLength(2));
    expect(screen.getByText(en.models.shared.observations.loading)).toBeInTheDocument();
    expect(screen.queryByText(en.models.shared.observations.staleRead)).not.toBeInTheDocument();
    await act(async () =>
      reply(metrics()[1], {
        errors: [{ message: "Read denied" }],
        data: { astroliftModelSubscriptionMetrics: null },
      })
    );
    await screen.findByText("Read denied");
    expect(screen.getByText(en.models.shared.observations.staleRead)).toBeInTheDocument();
    expect(screen.getByText("17")).toBeInTheDocument();
  });
  it("does not restore traffic selection after page A→B→A", async () => {
    render(
      <ModelSubscriptionsClient model={model} blocked={false} onRefreshDeployment={() => {}} />,
      { wrapper }
    );
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: en.models.shared.subscriptionUsage.view })
      ).toHaveLength(2)
    );
    fireEvent.click(
      screen.getAllByRole("button", { name: en.models.shared.subscriptionUsage.view })[0]
    );
    await screen.findByText("17");
    fireEvent.click(screen.getByRole("button", { name: en.shared.pagination.nextPage }));
    await waitFor(() =>
      expect(
        requests.some(
          (r) => r.operationName === "ListClusterModelSubscriptions" && r.variables.page === 2
        )
      ).toBe(true)
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.shared.pagination.previousPage })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: en.shared.pagination.previousPage }));
    await waitFor(() =>
      expect(
        requests.filter(
          (r) => r.operationName === "ListClusterModelSubscriptions" && r.variables.page === 1
        )
      ).toHaveLength(2)
    );
    expect(metrics()).toHaveLength(1);
    expect(screen.queryByText("17")).not.toBeInTheDocument();
  });
  it("drops held evidence when another subscription is selected", async () => {
    held = true;
    const v = render(view(), { wrapper });
    await waitFor(() => expect(metrics()).toHaveLength(1));
    const original = metrics()[0];
    v.rerender(view(model, { ...subscription, id: "subscription-two" }));
    await waitFor(() => expect(metrics()).toHaveLength(2));
    await act(async () => metricReply(original));
    expect(screen.queryByText("17")).not.toBeInTheDocument();
    await act(async () => metricReply(metrics()[1]));
    await screen.findByText("17");
    expect(metrics()[1].variables.subscriptionId).toBe("subscription-two");
  });
  it("keeps an unavailable null response distinct from no-data and refuses actorless reads", async () => {
    held = true;
    const v = render(view(), { wrapper });
    await waitFor(() => expect(metrics()).toHaveLength(1));
    await act(async () =>
      reply(metrics()[0], { data: { astroliftModelSubscriptionMetrics: null } })
    );
    await screen.findAllByText(en.models.shared.subscriptionUsage.unavailable);
    expect(screen.queryByText(en.models.shared.subscriptionUsage.empty)).not.toBeInTheDocument();
    identity.actor = "";
    v.rerender(view());
    expect(metrics()).toHaveLength(1);
  });
});
