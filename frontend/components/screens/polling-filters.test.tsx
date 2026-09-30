import { ApolloClient, ApolloLink, InMemoryCache, type Operation } from "@apollo/client";
import { ApolloProvider, useQuery } from "@apollo/client/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { Observable } from "rxjs";
import { afterEach, expect, it, vi } from "vitest";

import {
  LIST_MEMBERS_PAGE,
  LIST_ROLE_BINDINGS_PAGE,
  LIST_INVITATIONS_PAGE,
} from "@/graphql/identity/identity.queries";
import { LIST_SSH_DEPLOY_KEYS_PAGE, LIST_SOURCE_CONNECTIONS_PAGE } from "@/graphql/scm/scm.queries";
import { useTaskRuns } from "./apps/homes/use-task-runs";
import { useNotifications } from "./settings/security/use-notifications";
import { useTeamsCard } from "./apps/overview/use-teams-card";
import { useGrantRole } from "./members/use-grant-role";
import { useInviteDialog } from "./members/use-invite-dialog";
import {
  useGenerateSshKey,
  useAddClientId,
} from "./settings/source-providers/use-source-providers";
import { useEnvironmentControls } from "./apps/controls/use-controls-section";
import { ENV_PROD, DEPLOYMENT } from "./apps/controls/app-controls.fixtures";
import { useRecentClusterWorkflows } from "./clusters/status/use-recent-cluster-workflows";
import { useClusterLifecycleAudit } from "./clusters/status/use-cluster-lifecycle-audit";
import { useObservabilitySummary } from "./apps/overview/use-observability-summary";
import { AppSecurityScreen } from "./apps/security/AppSecurityScreen";
import { useAppSecurity } from "./apps/security/use-app-security";
import {
  APP,
  SIGNING_EVENT,
  SCAN_EVENT,
  SBOM_EVENT,
} from "./apps/security/app-security-previews.fixtures";
import { useAlertRuleDetail } from "./alerts/use-alert-rule-detail";
import { useAlertEventDetail } from "./alerts/use-alert-event-detail";
import { useEventDetail } from "./events/use-events";
import { useLocalListState } from "@/components/list/use-list-state";
import { AlertsScreen } from "./alerts/AlertsScreen";
import { AlertEventDetail } from "./alerts/AlertEventDetail";
import { AlertRuleDetail } from "./alerts/AlertRuleDetail";
import { NotificationsInbox } from "./settings/security/NotificationsInbox";
import { ALERT_RULES_LIST } from "./alerts/alerts-list";
import { alertsProps, EVENT_DETAIL, RULE_DETAIL, RULES, EVENTS } from "./alerts/alerts.fixtures";
import { useAppFrame } from "./apps/detail/use-app-frame";
import { useWorkflowInstancesList } from "./workflows/list/use-workflow-instances";
import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";
import { toast } from "sonner";

let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/test",
  useSearchParams: () => searchParams,
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org", slug: "org" }, loading: false }),
}));
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params?.remaining ? `Muted ${params.remaining}` : key,
}));
vi.mock("@/lib/i18n/formatters", () => ({
  useFormatters: () => ({
    formatDate: String,
    formatDateTime: String,
    formatRelativeTime: () => "now",
    formatNumber: String,
  }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false, granted: new Set() }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

type Data = Record<string, unknown>;
const transport = (response: (operation: Operation) => Data | Error) => {
  const requests: Array<{ name: string; variables: Record<string, unknown> }> = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push({
            name: operation.operationName ?? "",
            variables: structuredClone(operation.variables),
          });
          const value = response(operation);
          const timer = setTimeout(() => {
            if (value instanceof Error) observer.error(value);
            else {
              observer.next({ data: value });
              observer.complete();
            }
          }, 1);
          return () => clearTimeout(timer);
        })
    ),
  });
  return {
    requests,
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <ApolloProvider client={client}>{children}</ApolloProvider>
    ),
  };
};
const emptyPage = { items: [], nextCursor: null, totalCount: 0 };
const appResponse = {
  __typename: "AstroliftRegisteredApp",
  ...APP,
  ...Object.fromEntries(
    [
      "description",
      "organizationSlug",
      "teamSlug",
      "teamId",
      "teamName",
      "projectSlug",
      "projectId",
      "projectName",
      "sourceKind",
      "sourceRepo",
      "manifestPath",
      "defaultBranch",
      "manifestHash",
      "registryRepoUri",
      "ecrRepoUri",
      "ecrPushRoleArn",
      "providerPluginSlug",
      "k8sNamespace",
      "managedHostname",
      "provisioningStatus",
      "provisioningError",
      "deployTokenLast4",
      "triggerMode",
      "cronExpression",
      "deployBranch",
      "previewScreenshotUrl",
      "rawManifest",
      "rawManifestStaged",
      "rawManifestStagedHash",
      "lastSyncedHash",
      "manifestSyncState",
      "manifestBootstrapStatus",
      "manifestBootstrapError",
      "webhookDeploysPausedByEmail",
      "webhookDeploysPauseReason",
    ].map((key) => [key, ""])
  ),
  reprovision: { needsReprovision: false, state: "ready", reason: "", elapsedSeconds: 0 },
  isActive: true,
  provisioningProgress: null,
  logRetentionDays: 7,
  lastResyncAt: null,
  sourceWebhookInstalledAt: null,
  isArchived: false,
  archivedAt: null,
  webhookDeploysPaused: false,
  webhookDeploysPausedAt: null,
  activePreviewCount: 0,
  createdAt: "2026-09-27T00:00:00Z",
  updatedAt: "2026-09-27T00:00:00Z",
  deletedAt: null,
  version: 1,
  stagedEnvChanges: [],
  configDrift: null,
  autowire: null,
  ciWorkflowSyncStatus: null,
  settingsLastModified: {
    deployStrategy: null,
    deployTokens: null,
    secrets: null,
    managedServices: null,
    domains: null,
    webhooks: null,
    members: null,
    observability: null,
  },
  retentionPolicies: [],
};
const eventResponse = (event: typeof SIGNING_EVENT) => ({
  __typename: "AstroliftEvent",
  ...event,
  organizationId: "org",
  teamId: null,
  projectId: null,
  resourceKind: "app",
  resourceId: APP.id,
  severity: "info",
});

const defaults: Record<string, Data> = {
  ListTeams: { astroliftTeams: [] },
  ListProjects: { astroliftProjects: [] },
  ListAppTeamAccesses: { astroliftAppTeamAccesses: [] },
  ListAppTeamAccessesPage: { astroliftAppTeamAccessesPage: emptyPage },
  ListRolesICanGrant: { astroliftRolesICanGrant: [] },
  SearchableUsers: { astroliftSearchableUsers: [] },
  ListMembersPage: { astroliftMembersPage: emptyPage },
  ListRoleBindingsPage: { astroliftRoleBindingsPage: emptyPage },
  ListInvitationsPage: { astroliftInvitationsPage: emptyPage },
  ListSshDeployKeysPage: { astroliftSshDeployKeysPage: emptyPage },
  ListSourceConnectionsPage: { astroliftSourceConnectionsPage: emptyPage },
  ListSourceConnections: { astroliftSourceConnections: [] },
  GetApp: { astroliftApp: appResponse },
  ListEnvironments: { astroliftEnvironments: [] },
  ListWorkloads: { astroliftWorkloads: [] },
};
afterEach(() => {
  vi.useRealTimers();
  searchParams = new URLSearchParams();
  vi.clearAllMocks();
});

it("asks the server for this workload before the thirty-run limit", async () => {
  const api = transport(() => ({ astroliftTaskRuns: [] }));
  const hook = renderHook(() => useTaskRuns("app", "infrequent-job"), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  expect(api.requests[0].variables).toEqual({
    appSlug: "app",
    workloadSlug: "infrequent-job",
    limit: 30,
  });
});

it("refreshes the active team grant page immediately after Add team", async () => {
  let granted = false;
  const api = transport((operation) => {
    if (operation.operationName === "GrantTeamAccessToApp") {
      granted = true;
      return { grantTeamAccessToApp: { ok: true, errors: [], data: null } };
    }
    if (operation.operationName === "ListAppTeamAccessesPage")
      return { astroliftAppTeamAccessesPage: { ...emptyPage, totalCount: granted ? 1 : 0 } };
    return defaults[operation.operationName ?? ""] ?? {};
  });
  const hook = renderHook(
    () => useTeamsCard({ appSlug: "app", appId: "app-id", homeTeamSlug: "team" }),
    { wrapper: api.wrapper }
  );
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(async () => {
    await hook.result.current.onAddTeam("new-team", "viewer");
  });
  await waitFor(() => expect(hook.result.current.totalCount).toBe(1));
});

it("refreshes current member and grant pages without resetting their variables", async () => {
  const api = transport((o) =>
    o.operationName === "GrantRole"
      ? { grantRole: { ok: true, errors: [], data: null } }
      : (defaults[o.operationName ?? ""] ?? {})
  );
  const vars = { limit: 25, after: "older", search: "reba" };
  const hook = renderHook(
    () => {
      useQuery(LIST_MEMBERS_PAGE, { variables: vars, fetchPolicy: "network-only" });
      useQuery(LIST_ROLE_BINDINGS_PAGE, { variables: vars, fetchPolicy: "network-only" });
      return useGrantRole();
    },
    { wrapper: api.wrapper }
  );
  await waitFor(() =>
    expect(api.requests.some((r) => r.name === "ListRoleBindingsPage")).toBe(true)
  );
  await act(async () => {
    await hook.result.current.onGrant({
      userId: "user",
      roleId: "role",
      scopeKind: "ORG",
      scopeGuid: "org",
    });
  });
  for (const name of ["ListMembersPage", "ListRoleBindingsPage"]) {
    const reads = api.requests.filter((r) => r.name === name);
    expect(reads).toHaveLength(2);
    expect(reads[1].variables).toEqual(vars);
  }
});

it("refreshes the current invitation page when creating an invitation", async () => {
  const api = transport((o) =>
    o.operationName === "CreateInvitation"
      ? { createInvitation: { ok: true, errors: [], data: null } }
      : (defaults[o.operationName ?? ""] ?? {})
  );
  const vars = { limit: 25, after: "older", search: "example" };
  const hook = renderHook(
    () => {
      useQuery(LIST_INVITATIONS_PAGE, { variables: vars, fetchPolicy: "network-only" });
      return useInviteDialog(true);
    },
    { wrapper: api.wrapper }
  );
  await waitFor(() =>
    expect(api.requests.some((r) => r.name === "ListInvitationsPage")).toBe(true)
  );
  act(() => hook.result.current.setEmail("new@example.com"));
  await act(async () => {
    await hook.result.current.onSubmit();
  });
  const reads = api.requests.filter((r) => r.name === "ListInvitationsPage");
  expect(reads).toHaveLength(2);
  expect(reads[1].variables).toEqual(vars);
});

it.each(["key", "clientId"])(
  "refreshes the active %s provider page after a sheet write",
  async (kind) => {
    const api = transport((o) => {
      if (o.operationName === "GenerateSshDeployKey")
        return { generateSshDeployKey: { ok: true, errors: [], data: null } };
      if (o.operationName === "UpdateSourceConnection")
        return { updateSourceConnection: { ok: true, errors: [], data: null } };
      return defaults[o.operationName ?? ""] ?? {};
    });
    const vars = { limit: 25, after: "older", search: "production" };
    const hook = renderHook(
      () => {
        useQuery(kind === "key" ? LIST_SSH_DEPLOY_KEYS_PAGE : LIST_SOURCE_CONNECTIONS_PAGE, {
          variables: vars,
          fetchPolicy: "network-only",
        });
        const key = useGenerateSshKey();
        const id = useAddClientId();
        return { key, id };
      },
      { wrapper: api.wrapper }
    );
    const name = kind === "key" ? "ListSshDeployKeysPage" : "ListSourceConnectionsPage";
    await waitFor(() => expect(api.requests.some((r) => r.name === name)).toBe(true));
    await act(async () => {
      if (kind === "key") await hook.result.current.key.generate("new", null);
      else
        await hook.result.current.id.save(
          { id: "connection", name: "GitHub" } as AstroliftSourceConnection,
          "Iv23client"
        );
    });
    const reads = api.requests.filter((r) => r.name === name);
    expect(reads).toHaveLength(2);
    expect(reads[1].variables).toEqual(vars);
  }
);

it("refreshes the per-environment latest tag after deploying, before the next blank-tag deploy", async () => {
  let tag = "previous";
  const api = transport((o) => {
    if (o.operationName === "ListDeployments")
      return { astroliftDeployments: [{ ...DEPLOYMENT, imageTag: tag }] };
    if (o.operationName === "StartDeployment") {
      tag = (o.variables.input as { imageTag: string }).imageTag;
      return { startDeployment: { ok: true, errors: [], data: { ...DEPLOYMENT, imageTag: tag } } };
    }
    return {};
  });
  const hook = renderHook(() => useEnvironmentControls(ENV_PROD, "app", "main"), {
    wrapper: api.wrapper,
  });
  await waitFor(() => expect(hook.result.current.lastTag).toBe("previous"));
  await act(async () => {
    await hook.result.current.onDeploy("new-tag");
  });
  await waitFor(() => expect(hook.result.current.lastTag).toBe("new-tag"));
  await act(async () => {
    await hook.result.current.onDeploy("");
  });
  const writes = api.requests.filter((r) => r.name === "StartDeployment");
  expect((writes[1].variables.input as { imageTag: string }).imageTag).toBe("new-tag");
  expect(
    api.requests
      .filter((r) => r.name === "ListDeployments")
      .every((r) => r.variables.environmentName === ENV_PROD.name && r.variables.limit === 1)
  ).toBe(true);
});

it("mark-read refreshes the loaded inbox window rather than query defaults", async () => {
  const api = transport((o) =>
    o.operationName === "MarkNotificationRead"
      ? {
          markNotificationRead: {
            ok: true,
            errors: [],
            data: { id: "n", readAt: new Date().toISOString() },
          },
        }
      : { astroliftMyNotifications: [] }
  );
  const hook = renderHook(() => useNotifications(), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(async () => {
    await hook.result.current.onMarkRead("n");
  });
  expect(
    api.requests.filter((r) => r.name === "ListMyNotifications").map((r) => r.variables)
  ).toEqual([
    { unreadOnly: false, limit: 100 },
    { unreadOnly: false, limit: 100 },
  ]);
});

it("reports mark-all envelope rejection and an initial inbox read failure", async () => {
  const api = transport((o) =>
    o.operationName === "MarkAllNotificationsRead"
      ? { markAllNotificationsRead: { ok: false, errors: [], data: null } }
      : new Error("Permission denied")
  );
  const hook = renderHook(() => useNotifications(), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.error).toBe("Permission denied"));
  await act(async () => {
    await hook.result.current.onMarkAll();
  });
  expect(toast.error).toHaveBeenCalledWith("Could not mark notifications as read");
});

it.each(["workflow", "lifecycle"])(
  "polls the cluster status %s summary and stops after unmount",
  async (kind) => {
    vi.useFakeTimers();
    const api = transport(() =>
      kind === "workflow"
        ? { astroliftRecentClusterWorkflows: [] }
        : { astroliftClusterLifecycleAudit: [] }
    );
    const hook = renderHook(
      () =>
        kind === "workflow"
          ? useRecentClusterWorkflows("cluster", { limit: 5 })
          : useClusterLifecycleAudit("cluster", { limit: 5 }),
      { wrapper: api.wrapper }
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(kind === "workflow" ? 15020 : 30020);
    });
    expect(api.requests).toHaveLength(2);
    hook.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(api.requests).toHaveLength(2);
  }
);

it("reads all deployment activity pages and complete app alert counts", async () => {
  const items = (from: number, count: number) =>
    Array.from({ length: count }, (_, i) => ({
      id: `d-${from + i}`,
      status: "failed",
      createdAt: new Date().toISOString(),
      startedAt: null,
    }));
  const api = transport((o) =>
    o.operationName === "GetAppAlertSummary"
      ? { astroliftAlertEventSummary: { unresolvedCount: 61, criticalCount: 60 } }
      : {
          astroliftDeploymentsPage: o.variables.after
            ? { items: items(100, 50), nextCursor: null }
            : { items: items(0, 100), nextCursor: "second" },
        }
  );
  const hook = renderHook(() => useObservabilitySummary("app"), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.deploysLoading).toBe(false));
  expect(hook.result.current.totalDeploys).toBe(150);
  expect(hook.result.current.failedCount).toBe(150);
  expect(hook.result.current.unresolvedCount).toBe(61);
  expect(
    api.requests.filter((r) => r.name === "GetAppDeploymentActivity").map((r) => r.variables.after)
  ).toEqual([null, "second"]);
  expect(api.requests.every((r) => r.variables.appSlug === "app")).toBe(true);
});

it("reports a failed continuation instead of presenting partial activity as complete", async () => {
  const api = transport((o) =>
    o.operationName === "GetAppAlertSummary"
      ? { astroliftAlertEventSummary: { unresolvedCount: 0, criticalCount: 0 } }
      : o.variables.after
        ? new Error("Second page failed")
        : { astroliftDeploymentsPage: { items: [], nextCursor: "second" } }
  );
  const hook = renderHook(() => useObservabilitySummary("app"), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.error).toBe("Second page failed"));
});

it("fetches each security fact by app and event type without an organization window", async () => {
  const api = transport((o) =>
    o.operationName === "GetApp"
      ? { astroliftApp: appResponse }
      : {
          signing: [eventResponse(SIGNING_EVENT)],
          scan: [eventResponse(SCAN_EVENT)],
          sbom: [eventResponse(SBOM_EVENT)],
        }
  );
  const hook = renderHook(() => useAppSecurity(APP.slug), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.latestSbom?.id).toBe(SBOM_EVENT.id));
  expect(hook.result.current.latestSigning?.id).toBe(SIGNING_EVENT.id);
  expect(hook.result.current.latestScan?.id).toBe(SCAN_EVENT.id);
  expect(api.requests.find((r) => r.name === "GetAppSecurityEvents")?.variables).toEqual({
    appSlug: APP.slug,
  });
});

it.each(["event", "alert", "rule"])("reads an old %s directly by its ID", async (kind) => {
  const api = transport(() =>
    kind === "event"
      ? { astroliftEvent: eventResponse(SIGNING_EVENT) }
      : kind === "alert"
        ? { astroliftAlertEvent: EVENTS[0] }
        : { astroliftAlertRule: RULES[0] }
  );
  const id = kind === "event" ? SIGNING_EVENT.id : kind === "alert" ? EVENTS[0].id : RULES[0].id;
  const hook = renderHook(
    () =>
      kind === "event"
        ? useEventDetail(id)
        : kind === "alert"
          ? useAlertEventDetail(id)
          : useAlertRuleDetail(id),
    { wrapper: api.wrapper }
  );
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  expect(api.requests[0].variables).toEqual({ id });
});

it("keeps the app frame’s deploy action fresh when the Overview row is unmounted", async () => {
  vi.useFakeTimers();
  let tag = "old";
  const api = transport((o) =>
    o.operationName === "ListDeployments"
      ? { astroliftDeployments: [{ ...DEPLOYMENT, imageTag: tag }] }
      : (defaults[o.operationName ?? ""] ?? {})
  );
  const hook = renderHook(() => useAppFrame(APP.slug), { wrapper: api.wrapper });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(hook.result.current.latestDeploy?.imageTag).toBe("old");
  tag = "new";
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15020);
  });
  expect(hook.result.current.latestDeploy?.imageTag).toBe("new");
});

it("settles the workflow type filter before issuing a new request", async () => {
  vi.useFakeTimers();
  const api = transport(() => ({ astroliftWorkflowInstances: { items: [], nextCursor: null } }));
  const hook = renderHook(() => useWorkflowInstancesList(), { wrapper: api.wrapper });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  for (const type of ["D", "De", "Deploy"]) {
    searchParams = new URLSearchParams({ type });
    hook.rerender();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
  }
  expect(api.requests).toHaveLength(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(320);
  });
  expect(api.requests).toHaveLength(2);
  expect(api.requests[1].variables.workflowType).toBe("Deploy");
});

it("updates a mute countdown without a query refresh", async () => {
  vi.useFakeTimers();
  const muted = {
    ...RULES[1],
    activeMute: { ...RULES[1].activeMute!, ttlUntil: new Date(Date.now() + 120000).toISOString() },
  };
  const View = () => (
    <AlertsScreen {...alertsProps({ rows: [muted] })} list={useLocalListState(ALERT_RULES_LIST)} />
  );
  render(<View />);
  expect(screen.getByText("Muted 2m")).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(61000);
  });
  expect(screen.getByText("Muted 59s")).toBeInTheDocument();
});

it.each(["rule", "alert"])(
  "shows a failed %s read in its frame with retry, without claiming not found",
  (kind) => {
    const retry = vi.fn();
    render(
      kind === "rule" ? (
        <AlertRuleDetail {...RULE_DETAIL} rule={null} error="Permission denied" onRetry={retry} />
      ) : (
        <AlertEventDetail
          {...EVENT_DETAIL}
          event={null}
          error="Permission denied"
          onRetry={retry}
        />
      )
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Permission denied");
    expect(screen.queryByText(/not found/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  }
);

it("shows an initial notification read failure in the inbox instead of empty", async () => {
  const api = transport(() => new Error("Permission denied"));
  const View = () => <NotificationsInbox {...useNotifications()} />;
  render(<View />, { wrapper: api.wrapper });
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Permission denied"));
  expect(screen.queryByText("Inbox empty")).not.toBeInTheDocument();
});

it("keeps a committed notification write successful when its view refresh fails", async () => {
  let committed = false;
  const api = transport((o) => {
    if (o.operationName === "MarkAllNotificationsRead") {
      committed = true;
      return { markAllNotificationsRead: { ok: true, errors: [], data: { marked: 1 } } };
    }
    return committed ? new Error("Refresh failed") : { astroliftMyNotifications: [] };
  });
  const hook = renderHook(() => useNotifications(), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(async () => {
    await hook.result.current.onMarkAll();
  });
  expect(toast.success).toHaveBeenCalledWith("Cleared 1");
  expect(toast.warning).toHaveBeenCalled();
  expect(toast.error).not.toHaveBeenCalled();
});

it("retries a failed chart continuation from a fresh first page without duplicate rows", async () => {
  let failed = true;
  const items = (from: number, count: number) =>
    Array.from({ length: count }, (_, i) => ({
      id: `d-${from + i}`,
      status: "failed",
      createdAt: new Date().toISOString(),
      startedAt: null,
    }));
  const api = transport((o) => {
    if (o.operationName === "GetAppAlertSummary")
      return { astroliftAlertEventSummary: { unresolvedCount: 0, criticalCount: 0 } };
    if (o.variables.after && failed) return new Error("Second page failed");
    return {
      astroliftDeploymentsPage: o.variables.after
        ? { items: items(100, 50), nextCursor: null }
        : { items: items(0, 100), nextCursor: "second" },
    };
  });
  const hook = renderHook(() => useObservabilitySummary("app"), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.error).toBe("Second page failed"));
  failed = false;
  act(() => hook.result.current.onRetry());
  await waitFor(() => expect(hook.result.current.totalDeploys).toBe(150));
  expect(hook.result.current.error).toBeNull();
});

it("shows denied security reads in the app frame and retries the actual event query", async () => {
  let denied = true;
  const api = transport((o) =>
    o.operationName === "GetApp"
      ? { astroliftApp: appResponse }
      : denied
        ? new Error("Permission denied")
        : {
            signing: [eventResponse(SIGNING_EVENT)],
            scan: [eventResponse(SCAN_EVENT)],
            sbom: [eventResponse(SBOM_EVENT)],
          }
  );
  const View = () => <AppSecurityScreen {...useAppSecurity(APP.slug)} />;
  render(<View />, { wrapper: api.wrapper });
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Permission denied"));
  denied = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(api.requests.filter((r) => r.name === "GetAppSecurityEvents")).toHaveLength(2);
});
