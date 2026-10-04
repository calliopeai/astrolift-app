import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { buildSchema, parse, validate } from "graphql";
import { readFileSync } from "node:fs";
import { createServer, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTranslator } from "next-intl";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import type {
  ClusterModelFieldsFragment,
  ModelConnectionRequestFieldsFragment,
} from "@/graphql/__generated__/operations";
import { nativeModel } from "./native-model.fixtures";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import { connectionRequestRow, connectionPolicyProps } from "./model-connection.fixtures";
import { ModelConnectionIntakePanel } from "./ModelConnectionIntakePanel";
import { useModelConnectionIntake } from "./use-model-connection-intake";
import { ModelConnectionRequestClient } from "./ModelConnectionRequestClient";
import { ModelConnectionRequestsClient } from "./ModelConnectionRequestsClient";
import { ModelConnectionPolicyClient } from "./ModelConnectionPolicyClient";
import { ModelSubscriptionsClient } from "./ModelSubscriptionsClient";
const identity = vi.hoisted(() => ({
  org: "org",
  actor: "owner",
  loading: false,
  error: null as Error | null,
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({
    org: identity.org ? { id: identity.org } : null,
    loading: identity.loading,
    error: identity.error,
  }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({
    user: identity.actor ? { id: identity.actor } : null,
    loading: false,
    error: null,
  }),
}));
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
let model = sharedModelDetailProps.model!;
const requestGuid = "b83b44d8-a52d-4c4f-9efd-f4fa29172961";
const subscriptionGuid = "5f82114f-2b90-48e6-b14a-25d237ed7da5";
type Request = {
  operationName: string;
  query: string;
  variables: Record<string, unknown>;
  response: ServerResponse;
};
let server: Server,
  client: ApolloClient,
  requests: Request[],
  hold: Set<string>,
  failure: Set<string>,
  refuse: Set<string>,
  supported: boolean,
  action: "AUTO" | "REQUEST" | "DENY",
  row: ModelConnectionRequestFieldsFragment,
  policy: NonNullable<typeof connectionPolicyProps.policy>,
  replyOverride: Record<string, unknown> | null,
  hosting: boolean;
const writes = [
  "RequestModelConnection",
  "SubscribeClusterModel",
  "ApproveModelConnectionRequest",
  "RejectModelConnectionRequest",
  "CancelModelConnectionRequest",
  "FinalizeModelConnectionRequest",
  "UpdateOrganizationModelConnectionPolicy",
  "SetModelConnectionRestriction",
];
function reply(request: Request, data: Record<string, unknown>) {
  request.response.writeHead(200, { "content-type": "application/json" });
  request.response.end(JSON.stringify({ data }));
}
function handle(request: Request) {
  if (failure.has(request.operationName)) {
    request.response.writeHead(200, { "content-type": "application/json" });
    request.response.end(JSON.stringify({ errors: [{ message: "Raw current read unavailable" }] }));
    return;
  }
  const input = request.variables.input as Record<string, unknown> | undefined;
  switch (request.operationName) {
    case "GetModelConnectionCapabilities":
      reply(request, {
        astroliftServerInfo: { capabilities: supported ? ["models.connection_approvals"] : [] },
      });
      break;
    case "GetModelHostingAction":
      reply(request, {
        modelHostingAction: {
          allowed: hosting,
          reason: hosting ? null : "Raw platform operator denial",
        },
      });
      break;
    case "ListModelConnectionTargets":
      reply(request, {
        modelConnectionTargetsPage: {
          page: request.variables.page,
          pageSize: request.variables.pageSize,
          totalCount: 42,
          nextCursor: null,
          items: [
            {
              environmentId: "env-staging",
              environmentVersion: 4,
              appVersion: 7,
              clusterId: model.clusterId,
              appSlug: "storefront",
              environmentName: "staging",
              eligible: action !== "DENY",
              reason: action === "DENY" ? "Raw policy denies destination" : null,
              action,
              policyVersion: action === "DENY" ? null : "policy-revision",
              requiredApprovals: 2,
              allowSelfApproval: false,
            },
          ],
        },
      });
      break;
    case "GetModelConnectionAction":
      reply(request, {
        modelConnectionAction: {
          action,
          policyVersion: action === "DENY" ? null : "policy-revision",
          reason: action === "DENY" ? "Raw changed policy denies" : null,
          requiredApprovals: 2,
          allowSelfApproval: false,
        },
      });
      break;
    case "ListModelConnectionRequests":
      reply(request, {
        [request.variables.review ? "inbox" : "own"]: {
          page: request.variables.page,
          pageSize: request.variables.pageSize,
          totalCount: 51,
          nextCursor: null,
          items: [row],
        },
      });
      break;
    case "GetModelConnectionRequest":
      reply(request, { [request.variables.review ? "inbox" : "own"]: row });
      break;
    case "GetOrganizationModelConnectionPolicy":
      reply(request, { organizationModelConnectionPolicy: policy });
      break;
    case "GetModelConnectionRestriction":
      reply(request, { modelConnectionRestriction: policy });
      break;
    case "ListClusterModelSubscriptions":
      reply(request, {
        clusterModelSubscriptionsPage: {
          page: request.variables.page,
          pageSize: request.variables.pageSize,
          totalCount: 0,
          nextCursor: null,
          items: [],
        },
      });
      break;
    default: {
      if (!writes.includes(request.operationName))
        throw new Error(`Unexpected operation ${request.operationName}`);
      const field = request.operationName[0].toLowerCase() + request.operationName.slice(1);
      if (refuse.has(request.operationName)) {
        reply(request, {
          [field]: {
            ok: false,
            data: null,
            errors: [
              {
                code: "FORBIDDEN",
                message: "Literal server refusal 123",
                field: null,
                currentVersion: 9,
                requestedVersion: 2,
                requiresAttestation: false,
                supportedMethods: [],
              },
            ],
          },
        });
        break;
      }
      let data: unknown;
      if (request.operationName === "RequestModelConnection")
        data = { ...row, id: requestGuid, version: 1 };
      else if (request.operationName === "SubscribeClusterModel")
        data = {
          subscription: {
            id: "subscription-one",
            version: 1,
            modelDeploymentId: model.id,
            environmentId: "env-staging",
            alias: input?.alias,
            desiredEnabled: true,
            status: "pending",
            desiredRevision: 3,
            appId: "app-storefront",
            appSlug: "storefront",
            appName: "Storefront",
            environmentName: "staging",
            bindingPrefix: "MODEL_CHAT_",
            appliedRevision: 0,
            reason: null,
            canRevoke: false,
            reconcileStartedAt: null,
            reconciledAt: null,
          },
          restartRequired: true,
          deployment: {
            ...model,
            version: 6,
            status: "updating",
            operationId: "controlled-reconcile",
            operationCompletedAt: null,
            ready: model.sourceKind === "bedrock_foundation_model" ? null : false,
            desiredSubscriptionRevision: 3,
          },
        };
      else if (
        request.operationName.includes("Policy") ||
        request.operationName === "SetModelConnectionRestriction"
      ) {
        policy = {
          ...policy,
          version: policy.version + 1,
          mode: input!.mode as typeof policy.mode,
          requiredApprovals: input!.requiredApprovals as number,
          allowSelfApproval: input!.allowSelfApproval as boolean,
        };
        data = policy;
      } else {
        row = {
          ...row,
          version: row.version + 1,
          status:
            request.operationName === "RejectModelConnectionRequest"
              ? "REJECTED"
              : request.operationName === "CancelModelConnectionRequest"
                ? "CANCELLED"
                : "APPROVED",
          canApprove: false,
          canReject: false,
          canCancel: request.operationName === "ApproveModelConnectionRequest",
          canFinalize: request.operationName === "ApproveModelConnectionRequest",
          approvalCount:
            request.operationName === "ApproveModelConnectionRequest" ? 2 : row.approvalCount,
          subscriptionId:
            request.operationName === "FinalizeModelConnectionRequest" ? "subscription-one" : null,
        };
        data = row;
      }
      reply(request, { [field]: { ok: true, errors: [], data, ...replyOverride } });
    }
  }
}
beforeEach(async () => {
  model = sharedModelDetailProps.model!;
  identity.org = "org";
  identity.actor = "owner";
  identity.loading = false;
  identity.error = null;
  requests = [];
  hold = new Set();
  failure = new Set();
  refuse = new Set();
  supported = true;
  hosting = true;
  action = "REQUEST";
  row = { ...connectionRequestRow };
  policy = { ...connectionPolicyProps.policy! };
  replyOverride = null;
  sessionStorage.clear();
  localStorage.clear();
  server = createServer((incoming, response) => {
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.on("end", () => {
      const request = { ...JSON.parse(body), response } as Request;
      expect(validate(schema, parse(request.query))).toEqual([]);
      requests.push(request);
      if (!hold.has(request.operationName)) handle(request);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({ uri: `http://127.0.0.1:${address.port}/gql` }),
    devtools: { enabled: false },
  });
});
afterEach(async () => {
  client.stop();
  for (const request of requests) if (!request.response.writableEnded) request.response.end();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});
function wrapper(locale: keyof typeof catalogs = "en") {
  return function Provider({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="America/Costa_Rica"
      >
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
function Intake({
  current = model,
  refresh = () => {},
}: {
  current?: ClusterModelFieldsFragment;
  refresh?: () => unknown;
}) {
  return (
    <Context
      key={`${identity.actor}:${identity.org}:${current.id}:${current.clusterId}:${current.providerId}`}
      current={current}
      refresh={refresh}
    />
  );
}
function Context({
  current,
  refresh,
}: {
  current: ClusterModelFieldsFragment;
  refresh: () => unknown;
}) {
  return <ModelConnectionIntakePanel {...useModelConnectionIntake(current, false, refresh)} />;
}
async function intakeReview(locale: keyof typeof catalogs = "en") {
  const t = createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "models.shared.connections",
  });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "storefront / staging" })).toBeEnabled()
  );
  fireEvent.click(screen.getByRole("button", { name: "storefront / staging" }));
  fireEvent.change(screen.getByLabelText(t("alias")), { target: { value: "chat" } });
  fireEvent.click(screen.getByRole("button", { name: t("review") }));
  await waitFor(() => expect(screen.getByRole("alertdialog")).toBeVisible());
  return t;
}
async function confirm(label: string) {
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: label }));
}
function calls(name: string) {
  return requests.filter((request) => request.operationName === name);
}
describe("actual-schema connection intake", () => {
  it.each(Object.keys(catalogs) as (keyof typeof catalogs)[])(
    "%s records a request, not a subscription, through failed read",
    async (locale) => {
      render(<Intake />, { wrapper: wrapper(locale) });
      const t = await intakeReview(locale);
      failure.add("ListModelConnectionTargets");
      await confirm(t("requestApproval"));
      await waitFor(() => expect(screen.getByText(t("requestSaved"))).toBeVisible());
      await waitFor(() => expect(screen.getByText(t("refreshFailed"))).toBeVisible());
      expect(calls("SubscribeClusterModel")).toHaveLength(0);
      const input = calls("RequestModelConnection")[0].variables.input as Record<string, unknown>;
      expect(input).toMatchObject({
        organizationId: "org",
        modelDeploymentId: model.id,
        expectedClusterId: model.clusterId,
        expectedProviderId: model.providerId,
        appEnvironmentId: "env-staging",
        ifMatchAppVersion: 7,
        ifMatchEnvironmentVersion: 4,
        ifMatchVersion: model.version,
        policyVersion: "policy-revision",
        alias: "chat",
      });
      expect(input.idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);
      expect(screen.getByRole("link", { name: t("openRequest") })).toHaveAttribute(
        "href",
        `/models/connections/${requestGuid}?version=1`
      );
    }
  );
  it("retains refused draft without follow-up read or close", async () => {
    refuse.add("RequestModelConnection");
    render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    const reads = calls("ListModelConnectionTargets").length;
    await confirm(t("requestApproval"));
    await screen.findByText("Literal server refusal 123");
    expect(screen.getByRole("alertdialog")).toBeVisible();
    expect(screen.getByLabelText(t("alias"))).toHaveValue("chat");
    expect(calls("ListModelConnectionTargets")).toHaveLength(reads);
    expect(sessionStorage.length).toBe(0);
  });
  it("retains durable exact request key for a lost reply and replays it", async () => {
    failure.add("RequestModelConnection");
    const first = render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    await confirm(t("requestApproval"));
    await screen.findByText(t("uncertain"));
    const key = (calls("RequestModelConnection")[0].variables.input as Record<string, unknown>)
      .idempotencyKey;
    expect(sessionStorage.length).toBe(1);
    first.unmount();
    failure.delete("RequestModelConnection");
    render(<Intake />, { wrapper: wrapper() });
    await screen.findByText(t("recoveryAvailable"));
    fireEvent.click(screen.getByRole("button", { name: t("restoreReview") }));
    await waitFor(() => expect(screen.getByLabelText(t("alias"))).toHaveValue("chat"));
    await waitFor(() => expect(screen.getByRole("button", { name: t("review") })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: t("review") }));
    await waitFor(() => expect(screen.getByRole("alertdialog")).toBeVisible());
    await confirm(t("requestApproval"));
    await screen.findByText(t("requestSaved"));
    expect(
      (calls("RequestModelConnection")[1].variables.input as Record<string, unknown>).idempotencyKey
    ).toBe(key);
    expect(sessionStorage.length).toBe(0);
  });
  it("known accepted but uncorrelated reply never becomes a refused write", async () => {
    replyOverride = { data: { ...row, organizationId: "foreign" } };
    render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    await confirm(t("requestApproval"));
    await screen.findByText(t("acceptedUnverified"));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(sessionStorage.length).toBe(1);
    expect(screen.queryByRole("link", { name: t("openRequest") })).toBeNull();
  });
  it("a replay already finalized is recorded, never described as unconnected", async () => {
    replyOverride = {
      data: {
        ...row,
        id: requestGuid,
        version: 1,
        status: "APPROVED",
        subscriptionId: subscriptionGuid,
      },
    };
    render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    await confirm(t("requestApproval"));
    await screen.findByText(t("connectionRecorded"));
    expect(screen.queryByText(t("requestSaved"))).toBeNull();
  });
  it.each(["missing", "malformed", "subscription"])(
    "keeps known accepted %s identity unverified with recovery",
    async (kind) => {
      replyOverride = {
        data: {
          ...row,
          id: kind === "missing" ? undefined : kind === "malformed" ? "not-a-guid" : requestGuid,
          version: 1,
          subscriptionId: kind === "subscription" ? "not-a-guid" : null,
        },
      };
      render(<Intake />, { wrapper: wrapper() });
      const t = await intakeReview();
      await confirm(t("requestApproval"));
      await screen.findByText(t("acceptedUnverified"));
      expect(sessionStorage.length).toBe(1);
      expect(screen.queryByRole("link", { name: t("openRequest") })).toBeNull();
      expect(screen.queryByText(t("requestSaved"))).toBeNull();
    }
  );
  it("keeps a known accepted malformed error envelope unverified rather than discarding recovery", async () => {
    replyOverride = { ok: true, errors: null };
    render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    await confirm(t("requestApproval"));
    await screen.findByText(t("acceptedUnverified"));
    expect(sessionStorage.length).toBe(1);
    expect(screen.queryByRole("link", { name: t("openRequest") })).toBeNull();
  });
  it.each(["missing", "nonboolean", "malformedRefusal"])(
    "retains the exact recovery key for a %s envelope",
    async (kind) => {
      replyOverride =
        kind === "missing"
          ? { ok: undefined }
          : kind === "nonboolean"
            ? { ok: "false" }
            : { ok: false, errors: null };
      const first = render(<Intake />, { wrapper: wrapper() });
      const t = await intakeReview();
      await confirm(t("requestApproval"));
      await screen.findByText(t("uncertain"));
      expect(sessionStorage.length).toBe(1);
      const key = (calls("RequestModelConnection")[0].variables.input as Record<string, unknown>)
        .idempotencyKey;
      expect(screen.queryByText(t("requestSaved"))).toBeNull();
      first.unmount();
      replyOverride = null;
      render(<Intake />, { wrapper: wrapper() });
      await screen.findByText(t("recoveryAvailable"));
      fireEvent.click(screen.getByRole("button", { name: t("restoreReview") }));
      await waitFor(() => expect(screen.getByRole("button", { name: t("review") })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: t("review") }));
      await waitFor(() => expect(screen.getByRole("alertdialog")).toBeVisible());
      await confirm(t("requestApproval"));
      await screen.findByText(t("requestSaved"));
      expect(
        (calls("RequestModelConnection")[1].variables.input as Record<string, unknown>)
          .idempotencyKey
      ).toBe(key);
    }
  );
  it("AUTO connects only after current action recheck", async () => {
    action = "AUTO";
    render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    await confirm(t("connect"));
    await screen.findByText(t("connectionQueued"));
    expect(calls("RequestModelConnection")).toHaveLength(0);
    expect(calls("GetModelConnectionAction")).toHaveLength(1);
    expect(calls("SubscribeClusterModel")).toHaveLength(1);
  });
  it("accepted AUTO with foreign deployment metadata remains accepted but unverified", async () => {
    action = "AUTO";
    replyOverride = {
      data: {
        deployment: { ...model, providerId: "foreign" },
        subscription: {
          id: "subscription-one",
          version: 1,
          modelDeploymentId: model.id,
          environmentId: "env-staging",
          alias: "chat",
        },
        restartRequired: true,
      },
    };
    render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    await confirm(t("connect"));
    await screen.findByText(t("acceptedUnverified"));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.queryByText(t("connectionQueued"))).toBeNull();
  });
  it("changed policy refuses before mutation and preserves review", async () => {
    render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    action = "DENY";
    await confirm(t("requestApproval"));
    await screen.findByText("Raw changed policy denies");
    expect(writes.flatMap(calls)).toHaveLength(0);
    expect(screen.getByRole("alertdialog")).toBeVisible();
  });
  it("capability absence and first failed target read never reveal a fallback write", async () => {
    supported = false;
    render(<Intake />, { wrapper: wrapper() });
    await screen.findByText(en.models.shared.connections.unsupported);
    expect(calls("ListModelConnectionTargets")).toHaveLength(0);
    expect(writes.flatMap(calls)).toHaveLength(0);
  });
  it.each(["actor", "org"] as const)("discard late replies after %s ABA", async (field) => {
    hold.add("RequestModelConnection");
    const view = render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    await confirm(t("requestApproval"));
    await waitFor(() => expect(calls("RequestModelConnection")).toHaveLength(1));
    const previous = identity[field];
    identity[field] = "other";
    view.rerender(<Intake />);
    identity[field] = previous;
    view.rerender(<Intake />);
    await waitFor(() => expect(calls("ListModelConnectionTargets").length).toBeGreaterThan(1));
    await act(async () => {
      handle(calls("RequestModelConnection")[0]);
    });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.queryByText(t("requestSaved"))).toBeNull();
    expect(screen.getByLabelText(t("alias"))).toHaveValue("");
  });
  it("first failed destination read remains unavailable, retry re-reads the same placement", async () => {
    failure.add("ListModelConnectionTargets");
    render(<Intake />, { wrapper: wrapper() });
    await screen.findByText("Raw current read unavailable");
    expect(screen.queryByRole("button", { name: "storefront / staging" })).toBeNull();
    expect(screen.getByLabelText(en.models.shared.connections.alias)).toBeDisabled();
    failure.delete("ListModelConnectionTargets");
    fireEvent.click(screen.getByRole("button", { name: en.shared.list.retry }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "storefront / staging" })).toBeEnabled()
    );
    expect(calls("ListModelConnectionTargets")[1].variables.input).toEqual(
      calls("ListModelConnectionTargets")[0].variables.input
    );
  });
  it("old confirm cannot write after model version A→B→A", async () => {
    const view = render(<Intake />, { wrapper: wrapper() });
    const t = await intakeReview();
    view.rerender(<Intake current={{ ...model, version: model.version + 1 }} />);
    view.rerender(<Intake />);
    await waitFor(() =>
      expect(
        within(screen.getByRole("alertdialog")).getByRole("button", { name: t("requestApproval") })
      ).toBeDisabled()
    );
    expect(writes.flatMap(calls)).toHaveLength(0);
    expect(screen.getByLabelText(t("alias"))).toHaveValue("chat");
  });
  it("malformed local recovery data cannot become a request input", async () => {
    sessionStorage.setItem(
      `astrolift.model.connectionRequest:${JSON.stringify([identity.actor, identity.org, model.id, model.clusterId, model.providerId])}`,
      JSON.stringify({
        review: {
          organizationId: "org",
          modelId: model.id,
          clusterId: model.clusterId,
          providerId: model.providerId,
          alias: "bad alias",
        },
        request: { idempotencyKey: "not-a-guid" },
        appSlug: "storefront",
      })
    );
    render(<Intake />, { wrapper: wrapper() });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "storefront / staging" })).toBeEnabled()
    );
    expect(screen.queryByText(en.models.shared.connections.recoveryAvailable)).toBeNull();
  });
  it("production connections view never reads legacy targets and mounts intake separately", async () => {
    render(
      <ModelSubscriptionsClient model={model} blocked={false} onRefreshDeployment={() => {}} />,
      { wrapper: wrapper() }
    );
    await waitFor(() => expect(calls("ListClusterModelSubscriptions")).toHaveLength(1));
    expect(calls("ListModelSubscriptionTargets")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.connections.add }));
    await waitFor(() => expect(calls("ListModelConnectionTargets")).toHaveLength(1));
    expect(screen.queryByText(en.models.shared.subscriptions.emptyDescription)).toBeNull();
  });
});
describe("current request detail, reviewer and exact policy writes", () => {
  it("APPROVED remains unconnected until a separate current-version finalization", async () => {
    row = { ...row, status: "APPROVED", canFinalize: true, canCancel: true, approvalCount: 2 };
    render(<ModelConnectionRequestClient id={row.id} version={2} review={false} />, {
      wrapper: wrapper(),
    });
    await screen.findByText(en.models.shared.connections.approvedNotice);
    expect(calls("FinalizeModelConnectionRequest")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    failure.add("GetModelConnectionRequest");
    await confirm("Connect");
    await screen.findByText(en.models.shared.connections.connectionRecorded);
    await screen.findByText(en.models.shared.connections.refreshFailed);
    expect(calls("FinalizeModelConnectionRequest")[0].variables).toEqual({
      input: { id: "request-one", ifMatchVersion: 2 },
    });
  });
  it("adopts a fresh current version from an older URL and finalizes that observed version", async () => {
    row = { ...row, version: 2, status: "APPROVED", canFinalize: true };
    render(<ModelConnectionRequestClient id={row.id} version={1} review={false} />, {
      wrapper: wrapper(),
    });
    await screen.findByRole("button", { name: "Connect" });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await confirm("Connect");
    await screen.findByText(en.models.shared.connections.connectionRecorded);
    expect(calls("FinalizeModelConnectionRequest")[0].variables).toEqual({
      input: { id: row.id, ifMatchVersion: 2 },
    });
  });
  it("retry adopts the current reviewer row and a further read withdraws an open older review", async () => {
    failure.add("GetModelConnectionRequest");
    row = { ...row, version: 2, canApprove: true, canReject: true };
    render(<ModelConnectionRequestClient id={row.id} version={1} review />, { wrapper: wrapper() });
    await screen.findByText("Raw current read unavailable");
    failure.delete("GetModelConnectionRequest");
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.connections.retry }));
    await screen.findByRole("button", { name: "Approve" });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    row = { ...row, version: 3 };
    await act(async () => {
      await client.refetchQueries({ include: ["GetModelConnectionRequest"] });
    });
    expect(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Approve" })
    ).toBeDisabled();
    await confirm("Approve");
    expect(calls("ApproveModelConnectionRequest")).toHaveLength(0);
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Cancel",
      })
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await confirm("Approve");
    await screen.findByText(en.models.shared.connections.decisionSaved);
    expect(calls("ApproveModelConnectionRequest")[0].variables).toEqual({
      input: { id: row.id, ifMatchVersion: 3 },
    });
  });
  it("a read that returns a newer STALE row remains inspectable without actions", async () => {
    row = {
      ...row,
      version: 3,
      status: "STALE",
      canApprove: false,
      canReject: false,
      canCancel: false,
      canFinalize: false,
    };
    render(<ModelConnectionRequestClient id={row.id} version={1} review />, { wrapper: wrapper() });
    await screen.findByText(en.models.shared.connections.stale);
    expect(screen.getByText(row.id)).toBeVisible();
    expect(screen.queryByText(en.models.shared.connections.changed)).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(writes.flatMap(calls)).toHaveLength(0);
  });
  it("actor ABA requires a new production detail read before action hints reenable", async () => {
    row = { ...row, status: "APPROVED", canFinalize: true };
    const view = render(<ModelConnectionRequestClient id={row.id} version={2} review={false} />, {
      wrapper: wrapper(),
    });
    await screen.findByRole("button", { name: "Connect" });
    hold.add("GetModelConnectionRequest");
    identity.actor = "other";
    view.rerender(<ModelConnectionRequestClient id={row.id} version={2} review={false} />);
    await waitFor(() => expect(calls("GetModelConnectionRequest")).toHaveLength(2));
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
    identity.actor = "owner";
    view.rerender(<ModelConnectionRequestClient id={row.id} version={2} review={false} />);
    await waitFor(() => expect(calls("GetModelConnectionRequest")).toHaveLength(3));
    await act(async () => {
      handle(calls("GetModelConnectionRequest")[1]);
    });
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
    row = { ...row, canFinalize: false };
    await act(async () => {
      handle(calls("GetModelConnectionRequest")[2]);
    });
    await screen.findByText(row.id);
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
    expect(writes.flatMap(calls)).toHaveLength(0);
  });
  it("reviewer approval does not finalize or create a subscription", async () => {
    row = { ...row, canApprove: true, canReject: true, canCancel: false };
    render(<ModelConnectionRequestClient id={row.id} version={2} review />, { wrapper: wrapper() });
    await screen.findByRole("button", { name: "Approve" });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await confirm("Approve");
    await screen.findByText(en.models.shared.connections.decisionSaved);
    expect(calls("ApproveModelConnectionRequest")[0].variables).toEqual({
      input: { id: row.id, ifMatchVersion: 2 },
    });
    expect(calls("FinalizeModelConnectionRequest")).toHaveLength(0);
    expect(calls("SubscribeClusterModel")).toHaveLength(0);
  });
  it.each(["RejectModelConnectionRequest", "CancelModelConnectionRequest"])(
    "%s refusal does not refresh or close",
    async (operation) => {
      row = { ...row, canReject: true, canCancel: true };
      refuse.add(operation);
      render(
        <ModelConnectionRequestClient
          id={row.id}
          version={2}
          review={operation === "RejectModelConnectionRequest"}
        />,
        { wrapper: wrapper() }
      );
      const label =
        operation === "RejectModelConnectionRequest"
          ? "Reject"
          : en.models.shared.connections.cancelRequest;
      await screen.findByRole("button", { name: label });
      fireEvent.click(screen.getByRole("button", { name: label }));
      const reads = calls("GetModelConnectionRequest").length;
      await confirm(label);
      await screen.findByText("Literal server refusal 123");
      expect(screen.getByRole("alertdialog")).toBeVisible();
      expect(calls("GetModelConnectionRequest")).toHaveLength(reads);
    }
  );
  it("no server action flags means no review actions", async () => {
    row = { ...row, canApprove: false, canReject: false, canCancel: false, canFinalize: false };
    render(<ModelConnectionRequestClient id={row.id} version={2} review={false} />, {
      wrapper: wrapper(),
    });
    await screen.findByText(row.id);
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
  it("queue tabs execute only selected authorized endpoint, and offer no invented search", async () => {
    render(<ModelConnectionRequestsClient />, { wrapper: wrapper() });
    await screen.findByText(row.alias);
    expect(calls("ListModelConnectionRequests")[0].variables).toMatchObject({
      organizationId: "org",
      review: false,
      page: 1,
      pageSize: 25,
    });
    expect(screen.queryByRole("searchbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.connections.reviewInbox }));
    await waitFor(() => expect(calls("ListModelConnectionRequests")).toHaveLength(2));
    expect(calls("ListModelConnectionRequests")[1].variables.review).toBe(true);
    expect(screen.getByRole("link", { name: /Qwen production/ })).toHaveAttribute(
      "href",
      "/models/connections/request-one?version=2&review=1"
    );
  });
  it("refreshed request flag withdrawal disables an open review", async () => {
    row = { ...row, status: "APPROVED", canFinalize: true };
    const view = render(<ModelConnectionRequestClient id={row.id} version={2} review={false} />, {
      wrapper: wrapper(),
    });
    await screen.findByRole("button", { name: "Connect" });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    identity.loading = true;
    view.rerender(<ModelConnectionRequestClient id={row.id} version={2} review={false} />);
    identity.loading = false;
    row = { ...row, canFinalize: false };
    view.rerender(<ModelConnectionRequestClient id={row.id} version={2} review={false} />);
    await waitFor(() =>
      expect(
        within(screen.getByRole("alertdialog")).getByRole("button", { name: "Connect" })
      ).toBeDisabled()
    );
    expect(calls("FinalizeModelConnectionRequest")).toHaveLength(0);
  });
  it("held reviewer write cannot decorate a returned actor context", async () => {
    row = { ...row, canApprove: true, canReject: true };
    hold.add("ApproveModelConnectionRequest");
    const view = render(<ModelConnectionRequestClient id={row.id} version={2} review />, {
      wrapper: wrapper(),
    });
    await screen.findByRole("button", { name: "Approve" });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await confirm("Approve");
    await waitFor(() => expect(calls("ApproveModelConnectionRequest")).toHaveLength(1));
    identity.actor = "other";
    view.rerender(<ModelConnectionRequestClient id={row.id} version={2} review />);
    identity.actor = "owner";
    view.rerender(<ModelConnectionRequestClient id={row.id} version={2} review />);
    await act(async () => {
      handle(calls("ApproveModelConnectionRequest")[0]);
    });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.queryByText(en.models.shared.connections.decisionSaved)).toBeNull();
  });
  it.each(Object.keys(catalogs) as (keyof typeof catalogs)[])(
    "%s saves org policy with exact observed version, retaining committed outcome on failed read",
    async (locale) => {
      render(<ModelConnectionPolicyClient />, { wrapper: wrapper(locale) });
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "models.shared.connections",
      });
      await waitFor(() =>
        expect(screen.getByRole("button", { name: t("savePolicy") })).toBeEnabled()
      );
      fireEvent.change(screen.getByLabelText(t("quorum")), { target: { value: "3" } });
      fireEvent.click(screen.getByRole("button", { name: t("savePolicy") }));
      failure.add("GetOrganizationModelConnectionPolicy");
      await confirm(t("savePolicy"));
      await screen.findByText(t("policySaved"));
      await screen.findByText(t("refreshFailed"));
      expect(calls("UpdateOrganizationModelConnectionPolicy")[0].variables.input).toEqual({
        organizationId: "org",
        ifMatchVersion: 3,
        mode: "REQUIRE_APPROVAL",
        requiredApprovals: 3,
        allowSelfApproval: false,
      });
    }
  );
  it("refused policy write keeps quorum draft and exact raw diagnostic", async () => {
    refuse.add("UpdateOrganizationModelConnectionPolicy");
    render(<ModelConnectionPolicyClient />, { wrapper: wrapper() });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: en.models.shared.connections.savePolicy })
      ).toBeEnabled()
    );
    fireEvent.change(screen.getByLabelText(en.models.shared.connections.quorum), {
      target: { value: "4" },
    });
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.connections.savePolicy }));
    await confirm(en.models.shared.connections.savePolicy);
    await screen.findByText("Literal server refusal 123");
    expect(screen.getByLabelText(en.models.shared.connections.quorum)).toHaveValue(4);
    expect(calls("GetOrganizationModelConnectionPolicy")).toHaveLength(1);
  });
  it("model overlay read and update retain neutral absent version and exact placement", async () => {
    policy = { id: null, version: 0, mode: "AUTO", requiredApprovals: 1, allowSelfApproval: true };
    render(<ModelConnectionPolicyClient model={model} />, { wrapper: wrapper() });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: en.models.shared.connections.savePolicy })
      ).toBeEnabled()
    );
    expect(calls("GetModelConnectionRestriction")[0].variables.input).toEqual({
      organizationId: "org",
      modelDeploymentId: model.id,
      expectedClusterId: model.clusterId,
      expectedProviderId: model.providerId,
    });
    fireEvent.change(screen.getByLabelText(en.models.shared.connections.mode), {
      target: { value: "DENY" },
    });
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.connections.savePolicy }));
    await confirm(en.models.shared.connections.savePolicy);
    await screen.findByText(en.models.shared.connections.restrictionSaved);
    expect(calls("SetModelConnectionRestriction")[0].variables.input).toMatchObject({
      ifMatchVersion: 0,
      ifMatchDeploymentVersion: model.version,
      expectedClusterId: model.clusterId,
      expectedProviderId: model.providerId,
      mode: "DENY",
    });
  });
  it("non-superadmin host decision denies overlay read and write", async () => {
    hosting = false;
    render(<ModelConnectionPolicyClient model={model} />, { wrapper: wrapper() });
    await screen.findByText("Raw platform operator denial");
    expect(calls("GetModelConnectionRestriction")).toHaveLength(0);
    expect(calls("SetModelConnectionRestriction")).toHaveLength(0);
  });
});

describe("native app owner policy compatibility", () => {
  it.each(["AUTO", "REQUEST"] as const)(
    "preserves %s without hosting privileges or cloud runtime claims",
    async (nativeAction) => {
      model = {
        ...model,
        ...nativeModel,
        id: model.id,
        organizationId: model.organizationId,
        clusterId: model.clusterId,
        providerId: model.providerId,
        version: 5,
        name: model.name,
      };
      hosting = false;
      action = nativeAction;
      render(<Intake />, { wrapper: wrapper() });
      const t = await intakeReview();
      if (nativeAction === "AUTO")
        expect(screen.getByRole("alertdialog")).toHaveTextContent(
          en.models.native.details.restartImpact
        );
      await confirm(t(nativeAction === "AUTO" ? "connect" : "requestApproval"));
      await screen.findByText(t(nativeAction === "AUTO" ? "connectionQueued" : "requestSaved"));
      expect(calls("GetModelHostingAction")).toHaveLength(0);
      expect(calls("RegisterBedrockModel")).toHaveLength(0);
      expect(
        calls(nativeAction === "AUTO" ? "SubscribeClusterModel" : "RequestModelConnection")
      ).toHaveLength(1);
      expect(
        calls(nativeAction === "AUTO" ? "RequestModelConnection" : "SubscribeClusterModel")
      ).toHaveLength(0);
    }
  );
});
