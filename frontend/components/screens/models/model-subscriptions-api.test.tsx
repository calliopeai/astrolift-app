import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { ModelSubscriptionsClient } from "./ModelSubscriptionsClient";
import { ModelSubscriptionsPanel } from "./ModelSubscriptionsPanel";
import { useModelSubscriptions } from "./use-model-subscriptions";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";

const org = vi.hoisted(() => ({ id: "00000000-0000-4000-8000-000000000001" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: org.id }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: "current-subscription-reader" }, loading: false, error: null }),
}));
const model: ClusterModelFieldsFragment = {
  ...sharedModelDetailProps.model!,
  organizationId: org.id,
  id: "00000000-0000-4000-8000-000000000002",
  clusterId: "00000000-0000-4000-8000-000000000003",
  providerId: "00000000-0000-4000-8000-000000000004",
};
const production = "00000000-0000-4000-8000-000000000005",
  staging = "00000000-0000-4000-8000-000000000006",
  subscriptionId = "00000000-0000-4000-8000-000000000007";
type Request = { operationName: string; variables: Record<string, unknown> };
let requests: Request[], transport: (request: Request) => Promise<Response>, committed: boolean;
const subscription = {
  id: subscriptionId,
  version: 2,
  modelDeploymentId: model.id,
  appId: "00000000-0000-4000-8000-000000000008",
  appSlug: "storefront",
  appName: "Storefront",
  environmentId: production,
  environmentName: "production",
  alias: "chat",
  bindingPrefix: "MODEL_CHAT_",
  status: "active",
  canRevoke: true,
  desiredEnabled: true,
  desiredRevision: 3,
  appliedRevision: 3,
  reason: null,
  reconcileStartedAt: "2026-09-30T15:25:00Z",
  reconciledAt: "2026-09-30T15:30:00Z",
};
function response(data: Record<string, unknown>) {
  return new Response(JSON.stringify({ data }), {
    headers: { "Content-Type": "application/json" },
  });
}
function fixture(request: Request): Response {
  switch (request.operationName) {
    case "ListModelSubscriptionTargets":
      return response({
        clusterModelSubscriptionTargetsPage: {
          items: [production, staging].map((id, index) => ({
            environmentId: id,
            environmentVersion: index + 3,
            appId: subscription.appId,
            appSlug: "storefront",
            appName: "Storefront",
            environmentName: index ? "staging" : "production",
            clusterId: model.clusterId,
            eligible: !committed,
            reason: committed ? "Model reconciliation is pending" : null,
          })),
          totalCount: 22,
          nextCursor: null,
          page: request.variables.page,
          pageSize: request.variables.pageSize,
        },
      });
    case "ListClusterModelSubscriptions":
      return response({
        clusterModelSubscriptionsPage: {
          items: [subscription],
          totalCount: 21,
          nextCursor: null,
          page: request.variables.page,
          pageSize: request.variables.pageSize,
        },
      });
    case "SubscribeClusterModel":
    case "RevokeModelSubscription": {
      const input = request.variables.input as Record<string, unknown>,
        revoke = request.operationName === "RevokeModelSubscription";
      return response({
        [revoke ? "revokeModelSubscription" : "subscribeClusterModel"]: {
          ok: true,
          errors: [],
          data: {
            restartRequired: true,
            deployment: {
              ...model,
              version: 7,
              status: "updating",
              desiredSubscriptionRevision: 4,
              operationCompletedAt: null,
              ready: false,
              readinessObservedAt: null,
              readinessGeneration: null,
            },
            subscription: {
              ...subscription,
              version: 3,
              environmentId: revoke ? production : input.appEnvironmentId,
              environmentName: input.appEnvironmentId === staging ? "staging" : "production",
              alias: revoke ? "chat" : input.alias,
              bindingPrefix: revoke ? "MODEL_CHAT_" : `MODEL_${String(input.alias).toUpperCase()}_`,
              status: revoke ? "revoking" : "pending",
              desiredEnabled: !revoke,
              desiredRevision: 4,
              appliedRevision: 3,
              reconciledAt: null,
            },
          },
        },
      });
    }
    default:
      throw new Error(`Unexpected ${request.operationName}`);
  }
}
function wrapper() {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "http://control-plane.test/app/gql/config/",
      fetch: vi.fn(async (_uri, options) => {
        const request: Request = JSON.parse(String(options?.body));
        requests.push(request);
        return transport(request);
      }),
    }),
  });
  return function Provider({ children }: PropsWithChildren) {
    return (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
// Keep the legacy subscription transport contract exercised independently of
// the policy-aware production intake, covered by model-connection-http tests.
function LegacyTransportAdapter({
  current,
  refresh,
  blocked,
}: {
  current: ClusterModelFieldsFragment;
  refresh: () => void;
  blocked: boolean;
}) {
  return <ModelSubscriptionsPanel {...useModelSubscriptions(current, blocked, refresh)} />;
}
function client(current = model, refresh = vi.fn(), blocked = false) {
  return <LegacyTransportAdapter current={current} blocked={blocked} refresh={refresh} />;
}
function writes() {
  return requests.filter((request) =>
    ["SubscribeClusterModel", "RevokeModelSubscription"].includes(request.operationName)
  );
}
async function review(environment = "production") {
  fireEvent.click(await screen.findByRole("button", { name: `storefront / ${environment}` }));
  fireEvent.change(screen.getByLabelText("Subscription alias"), { target: { value: "assistant" } });
  fireEvent.click(screen.getByRole("button", { name: "Review subscription" }));
  await screen.findByRole("alertdialog");
}
beforeEach(() => {
  org.id = model.organizationId;
  requests = [];
  committed = false;
  transport = async (request) => fixture(request);
});

describe("legacy subscriptions and production revocation Apollo boundaries", () => {
  it.each(["production", "staging"])(
    "writes the exact %s target/version and named alias only after restart review",
    async (environment) => {
      const refresh = vi.fn();
      render(client(model, refresh), { wrapper: wrapper() });
      await review(environment);
      expect(writes()).toHaveLength(0);
      expect(screen.getByRole("alertdialog")).toHaveTextContent(
        en.models.shared.subscriptions.restartImpact
      );
      fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
      expect(await screen.findByText(en.models.shared.subscriptions.accepted)).toBeInTheDocument();
      expect(writes()).toHaveLength(1);
      expect(writes()[0].variables.input).toEqual({
        organizationId: model.organizationId,
        modelDeploymentId: model.id,
        expectedClusterId: model.clusterId,
        expectedProviderId: model.providerId,
        appEnvironmentId: environment === "production" ? production : staging,
        alias: "assistant",
        ifMatchVersion: 5,
        ifMatchEnvironmentVersion: environment === "production" ? 3 : 4,
      });
      expect(refresh).toHaveBeenCalledOnce();
      expect(
        requests.filter((request) => request.operationName === "ListClusterModelSubscriptions")
      ).toHaveLength(2);
    }
  );
  it("revokes the exact subscription and deployment revisions with independent destination admission", async () => {
    render(
      <ModelSubscriptionsClient
        model={{ ...model, subscriptionsEnabled: false }}
        blocked={false}
        onRefreshDeployment={vi.fn()}
      />,
      { wrapper: wrapper() }
    );
    fireEvent.click(await screen.findByRole("button", { name: "Review revocation" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("storefront");
    fireEvent.click(screen.getByRole("button", { name: "Request revocation" }));
    expect(await screen.findByText(en.models.shared.subscriptions.accepted)).toBeInTheDocument();
    expect(writes()[0]).toMatchObject({
      operationName: "RevokeModelSubscription",
      variables: {
        input: {
          organizationId: model.organizationId,
          id: subscriptionId,
          expectedClusterId: model.clusterId,
          expectedProviderId: model.providerId,
          ifMatchVersion: 2,
          ifMatchDeploymentVersion: 5,
        },
      },
    });
    expect(writes()).toHaveLength(1);
  });
  it("retains accepted pending state when follow-up reads fail, with no second write or false active claim", async () => {
    transport = async (request) => {
      if (request.operationName === "SubscribeClusterModel") {
        committed = true;
        return fixture(request);
      }
      if (committed)
        return new Response(JSON.stringify({ errors: [{ message: "Read refresh unavailable" }] }), {
          headers: { "Content-Type": "application/json" },
        });
      return fixture(request);
    };
    render(client(), { wrapper: wrapper() });
    await review();
    fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
    expect(await screen.findByText(en.models.shared.subscriptions.accepted)).toBeInTheDocument();
    expect((await screen.findAllByText("Read refresh unavailable")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Review subscription" })).toBeDisabled();
  });
  it.each(["denied", "mixed", "missing", "foreign", "wrong_alias", "premature_active"])(
    "keeps %s write refusal visible and the confirmation open",
    async (kind) => {
      transport = async (request) => {
        if (request.operationName !== "SubscribeClusterModel") return fixture(request);
        const result = await fixture(request).json(),
          envelope = result.data.subscribeClusterModel;
        if (kind === "denied" || kind === "mixed") {
          envelope.ok = kind === "mixed";
          envelope.errors = [
            {
              code: "VERSION_MISMATCH",
              message: "Refresh the selected environment",
              field: "ifMatchEnvironmentVersion",
              currentVersion: 9,
              requestedVersion: 3,
              requiresAttestation: false,
              supportedMethods: [],
            },
          ];
        }
        if (kind === "missing") envelope.data = null;
        if (kind === "foreign") envelope.data.subscription.environmentId = staging;
        if (kind === "wrong_alias") envelope.data.subscription.alias = "other";
        if (kind === "premature_active") envelope.data.subscription.status = "active";
        return response(result.data);
      };
      render(client(), { wrapper: wrapper() });
      await review();
      fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
      expect(
        await screen.findByText(
          kind === "denied" || kind === "mixed"
            ? "VERSION_MISMATCH: Refresh the selected environment"
            : en.models.shared.subscriptions.requestFailed
        )
      ).toBeInTheDocument();
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(screen.getByLabelText("Subscription alias")).toHaveValue("assistant");
      expect(screen.queryByText(en.models.shared.subscriptions.accepted)).not.toBeInTheDocument();
      expect(writes()).toHaveLength(1);
    }
  );
  it("reads server pages/search for targets and subscriptions without pretending a capped picker is complete", async () => {
    const { result } = renderHook(() => useModelSubscriptions(model, false, vi.fn()), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.targets.rows).toHaveLength(2));
    act(() => result.current.targets.list.setPage(3));
    await waitFor(() =>
      expect(requests.at(-1)).toMatchObject({
        operationName: "ListModelSubscriptionTargets",
        variables: { page: 3, pageSize: 10 },
      })
    );
    act(() => result.current.targets.list.applySearch("later app", {}));
    await waitFor(() =>
      expect(requests.at(-1)?.variables).toMatchObject({ search: "later app", page: 1 })
    );
    act(() => result.current.subscriptions.list.applySearch("assistant", {}));
    await waitFor(() =>
      expect(requests.at(-1)).toMatchObject({
        operationName: "ListClusterModelSubscriptions",
        variables: { search: "assistant", page: 1 },
      })
    );
    expect(writes()).toHaveLength(0);
  });
  it("discards late subscription after model version A→B→A without refreshing or replaying", async () => {
    let release!: (value: Response) => void;
    transport = async (request) =>
      request.operationName === "SubscribeClusterModel"
        ? new Promise<Response>((resolve) => {
            release = resolve;
          })
        : fixture(request);
    const refresh = vi.fn(),
      { rerender } = render(client(model, refresh), { wrapper: wrapper() });
    await review();
    fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    rerender(client({ ...model, version: 6 }, refresh));
    rerender(client(model, refresh));
    await act(async () => release(fixture(writes()[0])));
    expect(screen.queryByText(en.models.shared.subscriptions.accepted)).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Request subscription" })
    ).toBeDisabled();
    expect(refresh).not.toHaveBeenCalled();
    expect(writes()).toHaveLength(1);
  });
  it("refuses a changed target version even when an old hook callback is invoked directly", async () => {
    const { result, rerender } = renderHook(
      ({ current }) => useModelSubscriptions(current, false, vi.fn()),
      { initialProps: { current: model }, wrapper: wrapper() }
    );
    await waitFor(() => expect(result.current.targets.rows).toHaveLength(2));
    const stale = result.current.onSubscribe;
    rerender({ current: { ...model, version: 6 } });
    rerender({ current: model });
    await act(async () => {
      expect(
        await stale({
          organizationId: model.organizationId,
          modelId: model.id,
          modelVersion: 5,
          expectedClusterId: model.clusterId,
          expectedProviderId: model.providerId,
          environmentId: production,
          environmentVersion: 3,
          alias: "assistant",
        })
      ).toEqual({ accepted: false, message: en.models.shared.subscriptions.changed });
    });
    expect(writes()).toHaveLength(0);
  });
  it("does not borrow revoke or target authority from module visibility", async () => {
    transport = async (request) => {
      const result = await fixture(request).json();
      if (request.operationName === "ListClusterModelSubscriptions")
        result.data.clusterModelSubscriptionsPage.items[0].canRevoke = false;
      if (request.operationName === "ListModelSubscriptionTargets")
        result.data.clusterModelSubscriptionTargetsPage.items.forEach(
          (row: { eligible: boolean }) => {
            row.eligible = false;
          }
        );
      return response(result.data);
    };
    render(client(), { wrapper: wrapper() });
    expect(await screen.findByRole("button", { name: "Review revocation" })).toBeDisabled();
    expect(await screen.findByRole("button", { name: "storefront / production" })).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });
});
