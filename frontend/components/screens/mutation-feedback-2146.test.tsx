import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { getOperationAST } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/en.json";
import { AssignmentsView } from "./administration/permissions/AssignmentsView";
import { ToolDetailScreen } from "./agents/tools/ToolDetailScreen";
import { QR_PAYLOAD } from "./settings/security/settings-security-notifications.fixtures";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BINDINGS, OWNER_ROLE } from "./administration/permissions/fixtures";
import { useAssignments } from "./administration/permissions/use-assignments";
import { useNewRole } from "./administration/permissions/use-new-role";
import { useRoleDetail } from "./administration/permissions/use-role-detail";
import { useRoleHolders } from "./administration/permissions/use-role-holders";
import { useDispatchAgent } from "./agents/detail/use-dispatch-agent";
import { DETAIL } from "./agents/tools/agent-tools.fixtures";
import { useToolDetail } from "./agents/tools/use-tool-detail";
import { useSkillBuilder } from "./agents/skills/use-skill-builder";
import { GET_SKILL } from "@/graphql/agents/agents.queries";
import { AgentConfigFormPane } from "./apps/config/AgentConfigFormPane";
import { PANE_PROPS } from "./apps/config/app-config-agent.fixtures";
import { WORKLOAD_WEB } from "./apps/controls/app-controls.fixtures";
import { useEnvironmentControls, useWorkloadOps } from "./apps/controls/use-controls-section";
import { useDeployToken } from "./apps/controls/use-deploy-token";
import { ENVS } from "./apps/managed-services/app-managed-services.fixtures";
import { useManagedServices } from "./apps/managed-services/use-managed-services";
import { DEPLOY_RUNNING } from "./apps/deployments/app-deployments-logs.fixtures";
import { useDeploymentDetail } from "./deployments/use-deployment-detail";
import { useDeployments } from "./deployments/use-deployments";
import { useEnvironments } from "./environments/use-environments";
import { useAlertSubscriptions } from "./settings/security/use-alert-subscriptions";
import { usePairDevice } from "./settings/security/use-pair-device";
import { useTokens } from "./tokens/use-tokens";
import { useWebhooks } from "./webhooks/use-webhooks";
import { LIST_API_TOKENS_PAGE } from "@/graphql/identity/identity.queries";

const state = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  refetch: vi.fn(),
  push: vi.fn(),
  queries: {} as Record<string, unknown>,
}));
vi.mock("sonner", () => ({
  toast: { success: state.success, error: state.error, warning: state.warning },
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/apps",
}));
vi.mock("next-intl", async (original) => {
  const actual = await original<typeof import("next-intl")>();
  return {
    ...actual,
    useTranslations: (namespace?: string) =>
      namespace?.startsWith("shared.") ? actual.useTranslations(namespace) : (key: string) => key,
  };
});
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1" } }),
}));
// Keep the mutation client and network real; list fixtures isolate action handling
// from paging/polling, which have their own suites.
vi.mock("@apollo/client/react", async (original) => ({
  ...(await original<typeof import("@apollo/client/react")>()),
  useQuery: (doc: Parameters<typeof getOperationAST>[0]) => ({
    data: state.queries[getOperationAST(doc)?.name?.value ?? ""],
    loading: false,
    refetch: state.refetch,
  }),
  useSubscription: () => ({}),
}));
const { useQuery: useNetworkQuery } =
  await vi.importActual<typeof import("@apollo/client/react")>("@apollo/client/react");

let response: Record<string, unknown> | Error;
let requests: { operation: string; variables: Record<string, unknown> }[];
let queryFails: boolean;
let client: ApolloClient;
function wrapper({ children }: PropsWithChildren) {
  return (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
}
function fail(key: string) {
  response = {
    [key]: {
      ok: false,
      errors: [{ code: "denied", message: "Permission denied", field: null }],
      data: null,
    },
  };
}
function ok(key: string, data: unknown) {
  response = { [key]: { ok: true, errors: [], data } };
}
const draft = {
  name: "Release",
  slug: "release",
  description: "",
  scopeLevel: "ORG" as const,
  permissions: ["app.read"],
  duplicatedFromId: null,
};
const tokenInput = { name: "cli", scopes: ["read"], expiresInDays: "" };
const webhookInput = {
  url: "https://example.com",
  eventsRaw: "deploy.failed",
  format: "generic" as const,
};
const subscription = {
  id: "sub-1",
  appSlug: "api",
  alertKind: "deploy_failure",
  channel: "web",
  enabled: true,
};
const toolValues = {
  name: "Tool",
  slug: "tool",
  description: "",
  adapter: "python_fn" as const,
  handlerRef: "tools:run",
  inputSchemaText: "{}",
  outputSchemaText: "{}",
};
const page = { items: [], nextCursor: null, totalCount: 0, page: 1, pageSize: 25 };

beforeEach(() => {
  vi.clearAllMocks();
  requests = [];
  queryFails = false;
  response = new Error("Connection lost");
  state.refetch.mockResolvedValue({ data: {} });
  state.queries = {
    ListRoles: { astroliftRoles: [OWNER_ROLE] },
    GetRole: { astroliftRole: OWNER_ROLE },
    ListRoleBindingsPage: { astroliftRoleBindingsPage: page },
    GetToolDef: { toolDef: DETAIL.tool },
    GetDeployment: { astroliftDeployment: DEPLOY_RUNNING },
    ListDeploymentsPage: { astroliftDeploymentsPage: page },
    ListMyAlertSubscriptions: { astroliftMyAlertSubscriptions: [subscription] },
  };
  client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push({
            operation: operation.operationName ?? "",
            variables: operation.variables,
          });
          queueMicrotask(() => {
            if (getOperationAST(operation.query)?.operation === "query") {
              if (queryFails) observer.error(new Error("Refresh failed"));
              else {
                observer.next({
                  data: state.queries[operation.operationName ?? ""] ?? {
                    astroliftApiTokensPage: page,
                  },
                });
                observer.complete();
              }
            } else if (response instanceof Error) observer.error(response);
            else {
              observer.next({ data: response });
              observer.complete();
            }
          });
        })
    ),
  });
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

describe("role editor and confirms (#2146)", () => {
  it("returns rejected create/update messages to the editor without navigation", async () => {
    const create = renderHook(useNewRole, { wrapper });
    const update = renderHook(() => useRoleDetail(OWNER_ROLE.id), { wrapper });
    await act(async () => {
      expect(await create.result.current.onCreate(draft)).toBe("Connection lost");
      expect(await update.result.current.savePermissions(["app.read"])).toBe("Connection lost");
    });
    expect(state.push).not.toHaveBeenCalled();
    expect(state.success).not.toHaveBeenCalled();
  });
  it.each(["single", "holder", "bulk"])(
    "keeps %s revoke rejecting for ConfirmDialog",
    async (kind) => {
      const assignments = renderHook(useAssignments, { wrapper });
      const holders = renderHook(() => useRoleHolders(OWNER_ROLE), { wrapper });
      await act(async () => {
        if (kind === "bulk")
          await expect(assignments.result.current.onBulkRevoke(["b-1"])).rejects.toThrow(
            "Connection lost"
          );
        else
          await expect(
            (kind === "holder" ? holders : assignments).result.current.onRevoke(BINDINGS[0])
          ).rejects.toThrow("Connection lost");
      });
      expect(state.success).not.toHaveBeenCalled();
    }
  );
  it("rejects a failed bulk envelope", async () => {
    fail("bulkRevokeAstroliftRoleBindings");
    const { result } = renderHook(useAssignments, { wrapper });
    await act(async () => {
      await expect(result.current.onBulkRevoke(["b-1"])).rejects.toThrow("Permission denied");
    });
  });
});

describe("plain action failures (#2146)", () => {
  it.each([false, true])("surfaces %s pause/resume network rejection", async (paused) => {
    const { result } = renderHook(
      () => useEnvironmentControls({ ...ENVS[0], deploysPaused: paused }, "api", "main"),
      { wrapper }
    );
    await act(() => result.current.onTogglePause());
    expect(state.error).toHaveBeenCalledWith("Connection lost");
    expect(state.success).not.toHaveBeenCalled();
  });
  it("reports restart rejection and returns false from scale so its counter can revert", async () => {
    const workload = { ...WORKLOAD_WEB, id: "w-1", name: "api" };
    const { result } = renderHook(() => useWorkloadOps(workload), { wrapper });
    await act(async () => {
      await result.current.onRestart();
      expect(await result.current.onApply(3)).toBe(false);
    });
    expect(state.error).toHaveBeenCalledTimes(2);
    expect(state.success).not.toHaveBeenCalled();
  });
  it("reports deploy-token create rejection without claiming a mint", async () => {
    const { result } = renderHook(() => useDeployToken("api"), { wrapper });
    await act(() => result.current.onCreate());
    expect(state.error).toHaveBeenCalledWith("Connection lost");
    expect(result.current.reveal).toBeNull();
  });
  it.each(["transport", "envelope"])(
    "reports managed-service provision %s failure and keeps sheet open",
    async (kind) => {
      if (kind === "envelope") fail("provisionManagedService");
      const { result } = renderHook(() => useManagedServices("api"), { wrapper });
      await act(async () => {
        expect(
          await result.current.onProvision({
            environmentName: "prod",
            kind: "postgres",
            name: null,
            variant: null,
          })
        ).toBe(false);
      });
      expect(state.error).toHaveBeenCalledWith(
        kind === "transport" ? "Connection lost" : "Permission denied"
      );
    }
  );
  it("returns tool save rejection inline, retaining the edited values", async () => {
    const { result } = renderHook(() => useToolDetail("tool-1"), { wrapper });
    await act(async () => {
      expect(await result.current.save(toolValues)).toEqual({ form: "Connection lost" });
    });
    expect(state.success).not.toHaveBeenCalled();
  });
  it.each(["transport", "envelope"])(
    "reports clearing an alert subscription %s failure",
    async (kind) => {
      if (kind === "envelope") fail("clearAlertSubscription");
      const { result } = renderHook(useAlertSubscriptions, { wrapper });
      await act(() => result.current.onToggle("api", "deploy_failure", false));
      expect(requests[0].variables).toEqual({ input: { id: "sub-1" } });
      expect(state.error).toHaveBeenCalledWith(
        kind === "transport" ? "Connection lost" : "Permission denied"
      );
    }
  );
  it("keeps unsubscribe-all rejection for its confirmation", async () => {
    const { result } = renderHook(useAlertSubscriptions, { wrapper });
    await act(async () => {
      await expect(result.current.onUnsubscribeAll("api")).rejects.toThrow("Connection lost");
    });
  });
  it.each(["transport", "envelope"])(
    "reports environment resume %s failure while pause rejects",
    async (kind) => {
      if (kind === "envelope") fail("resumeEnvironment");
      const { result } = renderHook(useEnvironments, { wrapper });
      await act(() => result.current.onResume(ENVS[0]));
      expect(state.error).toHaveBeenCalledWith(
        kind === "transport" ? "Connection lost" : "Permission denied"
      );
      response = new Error("Connection lost");
      await act(async () => {
        await expect(result.current.onPause(ENVS[0])).rejects.toThrow("Connection lost");
      });
    }
  );
  it.each(["transport", "envelope"])("surfaces deployment Approve %s failure", async (kind) => {
    if (kind === "envelope") fail("approveDeployment");
    const { result } = renderHook(() => useDeploymentDetail(DEPLOY_RUNNING.id), { wrapper });
    await act(() => result.current.onApprove());
    expect(state.error).toHaveBeenCalledWith(
      kind === "transport" ? "Connection lost" : "Permission denied"
    );
  });
  it("refreshes bulk deployments after total failure while rejecting and resetting busy", async () => {
    const { result } = renderHook(useDeployments, { wrapper });
    await act(async () => {
      await expect(result.current.runBulk("abort", [DEPLOY_RUNNING], "cancel")).rejects.toThrow();
    });
    expect(state.refetch).toHaveBeenCalledTimes(1);
    expect(result.current.bulkRunning).toBe(false);
  });
  it.each(["token", "webhook"])(
    "reports %s create transport failure and leaves the sheet open",
    async (kind) => {
      const { result } = renderHook(() => ({ token: useTokens(), webhook: useWebhooks() }), {
        wrapper,
      });
      await act(async () => {
        expect(
          await (kind === "token"
            ? result.current.token.onCreate(tokenInput)
            : result.current.webhook.onCreate(webhookInput))
        ).toBe(false);
      });
      expect(state.error).toHaveBeenCalledWith("Connection lost");
      expect(result.current.token.createdToken).toBeNull();
      expect(result.current.webhook.reveal).toBeNull();
    }
  );
});

describe("dispatch commit and refresh", () => {
  it("returns the committed run id and reports refresh separately", async () => {
    ok("runAstroliftAgent", { id: "run-1", status: "queued", createdAt: "2026-09-29T00:00:00Z" });
    const after = vi.fn().mockRejectedValue(new Error("Refresh failed"));
    const { result } = renderHook(
      () => useDispatchAgent({ slug: "helper", name: "Helper" }, after),
      { wrapper }
    );
    await act(async () => {
      expect(await result.current.dispatch()).toBe("run-1");
    });
    expect(after).toHaveBeenCalledWith("run-1");
    expect(state.success).toHaveBeenCalledTimes(1);
    expect(state.warning).toHaveBeenCalledTimes(1);
    expect(state.error).not.toHaveBeenCalled();
  });
  it("does not refresh after a dispatch network failure", async () => {
    const after = vi.fn();
    const { result } = renderHook(
      () => useDispatchAgent({ slug: "helper", name: "Helper" }, after),
      { wrapper }
    );
    await act(async () => {
      expect(await result.current.dispatch()).toBeNull();
    });
    expect(after).not.toHaveBeenCalled();
    expect(state.error).toHaveBeenCalledTimes(1);
  });
});

describe("clipboard completion", () => {
  it.each(["missing", "denied"])(
    "reports config copy with %s clipboard and never claims success",
    async (kind) => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value:
          kind === "missing"
            ? undefined
            : { writeText: vi.fn().mockRejectedValue(new Error("Denied")) },
      });
      render(<AgentConfigFormPane {...PANE_PROPS} />);
      fireEvent.click(screen.getByRole("button", { name: "copy" }));
      await waitFor(() => expect(state.error).toHaveBeenCalledWith("copyFailed"));
      expect(state.success).not.toHaveBeenCalled();
    }
  );
  it("waits for the config clipboard write to finish", async () => {
    let finish!: () => void;
    navigator.clipboard.writeText = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        })
    );
    render(<AgentConfigFormPane {...PANE_PROPS} />);
    fireEvent.click(screen.getByRole("button", { name: "copy" }));
    expect(state.success).not.toHaveBeenCalled();
    await act(async () => finish());
    expect(state.success).toHaveBeenCalledWith("copied");
  });
  it.each(["uri", "payload"])("reports pairing %s copy failure", async (kind) => {
    ok("generateInstallEnrollmentQr", QR_PAYLOAD);
    const { result } = renderHook(usePairDevice, { wrapper });
    await act(() => result.current.onMint("phone"));
    navigator.clipboard.writeText = vi.fn().mockRejectedValue(new Error("Denied"));
    await act(() =>
      kind === "uri" ? result.current.onCopyVerificationUri() : result.current.onCopyPayload()
    );
    expect(state.error).toHaveBeenCalledWith("toastCopyFailed");
    expect(state.success).not.toHaveBeenCalled();
  });
});

describe("real Apollo refetch rejection", () => {
  it("keeps a committed skill save successful when its active query refresh fails", async () => {
    state.queries.GetSkill = {
      skill: {
        id: "skill-1",
        name: "Original",
        slug: "original",
        description: "",
        content: "",
        skillVersion: 1,
        isGlobal: false,
        isActive: true,
      },
    };
    ok("updateSkill", { id: "skill-1", slug: "edited" });
    const { result } = renderHook(
      () => {
        useNetworkQuery(GET_SKILL, { variables: { id: "skill-1" }, fetchPolicy: "network-only" });
        return useSkillBuilder("skill-1");
      },
      { wrapper }
    );
    await waitFor(() => expect(requests.some((r) => r.operation === "GetSkill")).toBe(true));
    queryFails = true;
    await act(async () => {
      expect(
        await result.current.saveSkill({
          name: "Edited",
          slug: "edited",
          description: "",
          content: "Edited content",
        })
      ).toEqual({});
    });
    expect(state.success).toHaveBeenCalledWith("Skill saved");
    // Apollo's onQueryUpdated passes a cache diff as its second argument.
    expect(state.warning).toHaveBeenCalledWith(
      "Could not refresh the view. Refresh to see current data."
    );
    expect(requests.filter((r) => r.operation === "GetSkill")).toHaveLength(2);
    expect(requests.filter((r) => r.operation === "UpdateSkill")).toHaveLength(1);
  });

  it("preserves a newly minted token and resolves success when the active list refetch fails", async () => {
    const reveal = {
      plaintext: "one-time-token",
      apiToken: {
        id: "token-1",
        name: "cli",
        tokenLast4: "oken",
        scopes: ["read"],
        expiresAt: null,
        lastUsedAt: null,
        lastUsedIp: "",
        lastUsedAgent: "",
        createdAt: "2026-09-29T00:00:00Z",
        isRevoked: false,
      },
    };
    ok("createApiToken", reveal);
    const { result } = renderHook(
      () => {
        useNetworkQuery(LIST_API_TOKENS_PAGE, {
          variables: { limit: 25, after: null, search: null },
          fetchPolicy: "network-only",
        });
        return useTokens();
      },
      { wrapper }
    );
    await waitFor(() =>
      expect(requests.some((r) => r.operation === "ListApiTokensPage")).toBe(true)
    );
    queryFails = true;
    await act(async () => {
      expect(await result.current.onCreate(tokenInput)).toBe(true);
    });
    expect(result.current.createdToken).toEqual(reveal);
    expect(state.warning).toHaveBeenCalled();
    expect(state.error).not.toHaveBeenCalled();
    expect(requests.filter((r) => r.operation === "CreateApiToken")).toHaveLength(1);
  });
});

describe("rendered action state", () => {
  it("keeps a failed bulk revoke confirmation open and its selection intact", async () => {
    state.queries.ListRoleBindingsPage = {
      astroliftRoleBindingsPage: { ...page, items: BINDINGS, totalCount: BINDINGS.length },
    };
    function Screen() {
      return <AssignmentsView {...useAssignments()} />;
    }
    render(<Screen />, { wrapper });
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    fireEvent.click(screen.getByRole("button", { name: "Revoke 1" }));
    fireEvent.click(screen.getByRole("alertdialog").querySelector("button:last-child")!);
    await waitFor(() => expect(state.error).toHaveBeenCalledWith("Connection lost"));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke 1" })).toBeInTheDocument();
  });
  it("shows a rejected tool save inline and retains the edited draft", async () => {
    function Screen() {
      return <ToolDetailScreen {...useToolDetail("tool-1")} />;
    }
    render(<Screen />, { wrapper });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Edited tool" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save tool" }));
    await waitFor(() => expect(screen.getByText("Connection lost")).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Edited tool");
    expect(screen.getByRole("button", { name: "Save tool" })).toBeEnabled();
  });
});

describe("envelope and follow-up failures", () => {
  it.each(["token", "webhook"])(
    "reports a denied %s create without a secret reveal",
    async (kind) => {
      fail(kind === "token" ? "createApiToken" : "createWebhookSubscription");
      const { result } = renderHook(() => ({ token: useTokens(), webhook: useWebhooks() }), {
        wrapper,
      });
      await act(async () => {
        expect(
          await (kind === "token"
            ? result.current.token.onCreate(tokenInput)
            : result.current.webhook.onCreate(webhookInput))
        ).toBe(false);
      });
      expect(state.error).toHaveBeenCalledWith("Permission denied");
    }
  );
  it("preserves a committed deploy-token reveal when its onCompleted refresh fails", async () => {
    ok("createDeployToken", {
      token: {
        id: "dt-1",
        name: "default",
        last4: "test",
        scopes: ["deploy"],
        isRevoked: false,
        lastUsedAt: null,
        lastRotatedAt: null,
        createdAt: "2026-09-29T00:00:00Z",
      },
      plaintextSecret: "one-time-deploy-token",
    });
    state.refetch.mockRejectedValue(new Error("Refresh failed"));
    const { result } = renderHook(() => useDeployToken("api"), { wrapper });
    await act(() => result.current.onCreate());
    expect(result.current.reveal).toBe("one-time-deploy-token");
    expect(state.success).toHaveBeenCalledTimes(1);
    expect(state.warning).toHaveBeenCalledTimes(1);
    expect(state.error).not.toHaveBeenCalled();
  });
  it("refresh failure does not replace the original bulk failure", async () => {
    state.refetch.mockRejectedValue(new Error("Refresh failed"));
    const { result } = renderHook(useDeployments, { wrapper });
    await act(async () => {
      await expect(result.current.runBulk("redeploy", [DEPLOY_RUNNING])).rejects.toThrow(
        "bulk.toasts.allFailed"
      );
    });
    expect(result.current.bulkRunning).toBe(false);
    expect(state.warning).toHaveBeenCalledTimes(1);
  });
  it("stops subscribe-all and avoids success when a cell is denied", async () => {
    fail("setAlertSubscription");
    const { result } = renderHook(useAlertSubscriptions, { wrapper });
    await act(() => result.current.onSubscribeAll("api"));
    expect(state.error).toHaveBeenCalledWith("Permission denied");
    expect(state.success).not.toHaveBeenCalled();
    expect(requests.filter((r) => r.operation === "SetAlertSubscription")).toHaveLength(1);
  });
  it("reports unavailable pairing clipboard", async () => {
    ok("generateInstallEnrollmentQr", QR_PAYLOAD);
    const { result } = renderHook(usePairDevice, { wrapper });
    await act(() => result.current.onMint("phone"));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    await act(() => result.current.onCopyPayload());
    expect(state.error).toHaveBeenCalledWith("toastCopyFailed");
    expect(state.success).not.toHaveBeenCalled();
  });
});
