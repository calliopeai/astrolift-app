import { renderWithIntl as render } from "@/test/render-with-intl";
import { ApolloClient, ApolloLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { Observable } from "rxjs";
import { describe, expect, it, vi } from "vitest";

import { TaskHomeScreen } from "./apps/homes/TaskHome";
import { useTaskRuns } from "./apps/homes/use-task-runs";
import { useManifestPreview } from "./apps/config/use-manifest-preview";
import { useManagedServicesSummary } from "./apps/overview/use-managed-services-summary";
import { useTriggerBindings } from "./agents/detail/use-trigger-bindings";
import { useAgentVncPopout } from "./agents/runs/use-agent-vnc-popout";
import { RUNNING_TASK } from "./agents/runs/agent-runs.fixtures";
import { useSecretProposalsQueue } from "./approvals/use-secret-proposals-queue";
import { useSecretProposalDetail } from "./approvals/use-secret-proposal-detail";
import { useDrivers } from "./documentation/use-drivers";
import { useEventRate } from "./events/use-events";
import { EventRate } from "./events/EventRate";
import { usePipelineDetail } from "./pipelines/use-pipeline-detail";
import { usePipelineSecrets } from "./pipelines/use-pipeline-secrets";
import { useCloudProviders } from "./providers/use-cloud-providers";
import { useDeployModel } from "./models/use-deploy-model";
import { useTrustedDomains } from "./administration/organization/use-trusted-domains";
import { useModulesCard } from "./administration/organization/use-modules-card";
import { useOps } from "./ops/use-ops";

import { useWorkflowFrame } from "./workflows/detail/use-workflow-frame";
import { WORKFLOW } from "./workflows/detail/workflow-detail-b.fixtures";
import { useSkillsList } from "./agents/skills/use-skills-list";
import { SKILL_ITEMS } from "./agents/skills/agent-skills.fixtures";
import { useAgentRunDetail } from "./agents/runs/use-agent-run-detail";
import { useFormDetail } from "./forms/use-form-detail";
import { FormSubmissionsTab } from "./forms/FormSubmissionsTab";
import { useAppLogs } from "./apps/deployments/use-app-logs";
import { useAppDomains } from "./apps/domains/use-app-domains";
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1" }, loading: false }),
}));
vi.mock("@/graphql/workflows/tiered.hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/graphql/workflows/tiered.hooks")>()),
  useWorkflowsEntitlement: () => ({ canRun: true, canManage: true }),
}));
vi.mock("@/lib/i18n/formatters", () => ({
  useFormatters: () => ({
    formatRelativeTime: () => "just now",
    formatNumber: String,
    formatDateTime: String,
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/test",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next-intl", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next-intl")>();
  return {
    ...actual,
    useTranslations: (namespace?: string) =>
      namespace?.startsWith("shared.") ? actual.useTranslations(namespace) : (key: string) => key,
  };
});
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));

type Payload =
  | Record<string, unknown>
  | null
  | ((operationName: string) => Record<string, unknown> | null);
const transport = (initial: Payload = null, delayFor: (operation: string) => number = () => 5) => {
  let payload = initial;
  let requests = 0;
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests++;
          const next =
            typeof payload === "function" ? payload(operation.operationName ?? "") : payload;
          const timer = setTimeout(
            () => {
              if (next === null) observer.error(new Error("Permission denied"));
              else {
                observer.next({ data: next });
                observer.complete();
              }
            },
            delayFor(operation.operationName ?? "")
          );
          return () => clearTimeout(timer);
        })
    ),
  });
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <ApolloProvider client={client}>{children}</ApolloProvider>
    ),
    setPayload: (next: Payload) => {
      payload = next;
    },
    requests: () => requests,
  };
};

const probes: Array<{
  name: string;
  use: () => { error: string | { message: string } | null; onRetry: () => void };
}> = [
  { name: "task runs", use: () => useTaskRuns("app", "job") },
  { name: "manifest", use: () => useManifestPreview("app") },
  { name: "managed services", use: () => useManagedServicesSummary("app") },
  {
    name: "trigger bindings",
    use: () => useTriggerBindings({ agentSlug: "agent", agentName: "Agent", orgId: "org" }),
  },
  { name: "VNC session", use: () => useAgentVncPopout("task") },
  { name: "secret proposal queue", use: () => useSecretProposalsQueue() },
  { name: "secret proposal detail", use: () => useSecretProposalDetail("proposal") },
  { name: "drivers", use: () => useDrivers() },
  { name: "event rate", use: () => useEventRate() },
  { name: "pipeline secrets", use: () => usePipelineSecrets("pipeline") },
  { name: "cloud providers", use: () => useCloudProviders() },
  { name: "modules", use: () => useModulesCard() },
];

describe.each(probes)("$name query states", ({ use: useProbe }) => {
  it("exposes a transport refusal and retries the actual Apollo query", async () => {
    const network = transport();
    const { result, unmount } = renderHook(useProbe, { wrapper: network.wrapper });
    await waitFor(() => expect(result.current.error).toBeTruthy());
    const previous = network.requests();
    act(() => result.current.onRetry());
    await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
    unmount();
    network.client.stop();
  });
});

it("keeps task frames loading, shows a failure instead of no runs, and retries into a real empty result", async () => {
  const network = transport();
  const Home = () => <TaskHomeScreen {...useTaskRuns("app", "job")} name="Job" />;
  render(<Home />, { wrapper: network.wrapper });
  expect(screen.queryByText("No runs yet")).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
  expect(screen.queryByText("No runs yet")).not.toBeInTheDocument();
  expect(screen.queryByText(/No run recorded yet/)).not.toBeInTheDocument();
  network.setPayload({ astroliftTaskRuns: [] });
  fireEvent.click(screen.getAllByRole("button", { name: "Retry" })[0]);
  await screen.findByText("No runs yet");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("distinguishes failed event rate from a successful zero-event window", async () => {
  const network = transport();
  const Rate = () => <EventRate {...useEventRate()} />;
  render(<Rate />, { wrapper: network.wrapper });
  expect(screen.queryByText("No events in this window")).not.toBeInTheDocument();
  await screen.findByRole("alert");
  expect(screen.queryByText("No events in this window")).not.toBeInTheDocument();
  network.setPayload({
    astroliftEventsPage: { __typename: "EventPage", items: [], nextCursor: null, totalCount: 0 },
  });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("No events in this window");
});

it("retains a running VNC task after a failed background refresh", async () => {
  const network = transport({ agentTask: RUNNING_TASK });
  const { result } = renderHook(() => useAgentVncPopout(RUNNING_TASK.id), {
    wrapper: network.wrapper,
  });
  await waitFor(() => expect(result.current.task?.status).toBe("running"));
  network.setPayload(null);
  const previous = network.requests();
  act(() => result.current.onRetry());
  await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.task?.id).toBe(RUNNING_TASK.id);
  expect(result.current.error).toBeNull();
});

it("does not turn failed driver reads into static provider facts", async () => {
  const network = transport();
  const { result } = renderHook(() => useDrivers(), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.error).toBeTruthy());
  expect(result.current.rows).toEqual([]);
  expect(result.current.usingFallback).toBe(false);
});

it("exposes both environment and manifest failures independently", async () => {
  const network = transport();
  const { result } = renderHook(() => useManifestPreview("app"), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.error).toBeTruthy());
  expect(result.current.environmentsError?.message).toBe("Permission denied");
});

it("exposes default-role refusal independently of an empty domain allowlist", async () => {
  const network = transport();
  const { result } = renderHook(() => useTrustedDomains(), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.rolesError?.message).toBe("Permission denied"));
  const previous = network.requests();
  act(() => result.current.onRetryRoles());
  await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
});

it("exposes pipeline run refusal with a retry", async () => {
  const network = transport();
  const { result } = renderHook(() => usePipelineDetail("pipeline"), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.runsError?.message).toBe("Permission denied"));
  const previous = network.requests();
  act(() => result.current.onRetryRuns());
  await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
});

it("does not treat unavailable GPU capabilities as an empty cluster list", async () => {
  const network = transport();
  const { result } = renderHook(() => useDeployModel(), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.clustersError).toBe("Permission denied"));
  expect(result.current.envsError).toBe("Permission denied");
  const previous = network.requests();
  act(() => result.current.onRetryClusters());
  await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
});

it("exposes each Ops frame refusal rather than deriving zeros or all clear", async () => {
  const network = transport();
  const { result } = renderHook(() => useOps(), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.metricsError).toBeTruthy());
  expect(result.current.clustersError?.message).toBe("Permission denied");
  expect(result.current.alertsError?.message).toBe("Permission denied");
  expect(result.current.auditError?.message).toBe("Permission denied");
  expect(result.current.runsError?.message).toBe("Permission denied");
});

it("keeps a configured workflow loaded when its refresh fails", async () => {
  const network = transport({ workflow: WORKFLOW, workflowDefinition: null });
  const { result } = renderHook(() => useWorkflowFrame(WORKFLOW.slug), {
    wrapper: network.wrapper,
  });
  await waitFor(() => expect(result.current.frame.workflow?.name).toBe(WORKFLOW.name));
  network.setPayload(null);
  const previous = network.requests();
  act(() => result.current.frame.onRetry?.());
  await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
  await waitFor(() => expect(result.current.frame.loading).toBe(false));
  expect(result.current.framed?.kind).toBe("configured");
  expect(result.current.frame.workflow?.name).toBe(WORKFLOW.name);
  expect(result.current.frame.error).toBeNull();
});

it("keeps skill rows visible after a failed background refresh", async () => {
  const network = transport({
    skillsPage: { items: SKILL_ITEMS, totalCount: SKILL_ITEMS.length, page: 1, pageSize: 25 },
  });
  const { result } = renderHook(() => useSkillsList(), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.rows).toHaveLength(SKILL_ITEMS.length));
  network.setPayload(null);
  const previous = network.requests();
  act(() => result.current.onRetry());
  await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.rows).toHaveLength(SKILL_ITEMS.length);
  expect(result.current.error).toBeNull();
});

it("keeps an agent run after a failed five-second poll refresh", async () => {
  const network = transport({ agentTask: RUNNING_TASK, agentTaskLogs: [] });
  const { result } = renderHook(() => useAgentRunDetail(RUNNING_TASK.id), {
    wrapper: network.wrapper,
  });
  await waitFor(() => expect(result.current.task?.id).toBe(RUNNING_TASK.id));
  network.setPayload(null);
  const previous = network.requests();
  act(() => result.current.onRetry());
  await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.task?.id).toBe(RUNNING_TASK.id);
  expect(result.current.error).toBeNull();
});

it("shows and retries a failed submissions request instead of claiming there are no submissions", async () => {
  const network = transport();
  const Submissions = () => {
    const state = useFormDetail("form");
    return (
      <FormSubmissionsTab
        submissions={state.submissions}
        loading={state.submissionsLoading}
        error={state.submissionsError}
        onRetry={state.onRetrySubmissions}
      />
    );
  };
  render(<Submissions />, { wrapper: network.wrapper });
  await screen.findByRole("alert");
  expect(screen.queryByText(/No submissions yet/)).not.toBeInTheDocument();
  network.setPayload({ formSubmissions: [] });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText(/No submissions yet/);
});

it("exposes app lookup failure separately from the logs stream", async () => {
  const network = transport();
  const { result } = renderHook(
    () => useAppLogs({ slug: "app", selectedPod: null, selectedContainer: null }),
    { wrapper: network.wrapper }
  );
  await waitFor(() => expect(result.current.appError?.message).toBe("Permission denied"));
  expect(result.current.error).toBeNull();
  const previous = network.requests();
  act(() => result.current.onRetryApp());
  await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
});

it("does not treat a failed environment lookup as an empty domains list", async () => {
  const network = transport();
  const { result } = renderHook(() => useAppDomains("app"), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.error?.message).toBe("Permission denied"));
  const previous = network.requests();
  act(() => result.current.refetch());
  await waitFor(() => expect(network.requests()).toBeGreaterThan(previous));
});

it("surfaces an environment failure even when domains and workloads loaded successfully", async () => {
  const network = transport((operation) =>
    operation === "ListEnvironments" ? null : { astroliftAppDomains: [], astroliftWorkloads: [] }
  );
  const { result } = renderHook(() => useAppDomains("app"), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.error?.message).toBe("Permission denied"));
  expect(result.current.loading).toBe(false);
  network.setPayload({
    astroliftEnvironments: [],
    astroliftAppDomains: [],
    astroliftWorkloads: [],
  });
  act(() => result.current.refetch());
  await waitFor(() => expect(result.current.error).toBeNull());
});

it("does not call a cloud provider unconfigured when the clusters query fails", async () => {
  const network = transport((operation) =>
    operation === "ListClusters" ? null : { astroliftProviderPlugins: [] }
  );
  const { result } = renderHook(() => useCloudProviders(), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.error?.message).toBe("Permission denied"));
  expect(result.current.loading).toBe(false);
});

it("does not invent no-cluster driver counts when only the cluster query fails", async () => {
  const network = transport((operation) =>
    operation === "ListClusters" ? null : { astroliftProviderPlugins: [] }
  );
  const { result } = renderHook(() => useDrivers(), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.error?.message).toBe("Permission denied"));
  expect(result.current.loading).toBe(false);
});

it("does not claim the install turned modules off when the install handshake failed", async () => {
  const me = {
    id: "viewer",
    profile: { id: "profile", username: "viewer" },
    modules: [
      {
        key: "chat_studio_integration",
        canView: true,
        canCreate: false,
        canManage: true,
        canRun: false,
        enabled: true,
      },
    ],
  };
  const network = transport((operation) => (operation === "AstroliftServerInfo" ? null : { me }));
  const { result } = renderHook(() => useModulesCard(), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.error?.message).toBe("Permission denied"));
  expect(result.current.items[0].enabled).toBe(true);
  network.setPayload({
    me,
    astroliftServerInfo: {
      featureFlags: [{ key: "modules.chat_studio_integration_allowed", enabled: true }],
    },
  });
  act(() => result.current.onRetry());
  await waitFor(() => expect(result.current.error).toBeNull());
  expect(result.current.items[0].installAllowed).toBe(true);
});

it("keeps modules loading until their install allowance is known", async () => {
  const me = {
    id: "viewer",
    profile: { id: "profile", username: "viewer" },
    modules: [
      {
        key: "chat_studio_integration",
        canView: true,
        canCreate: false,
        canManage: true,
        canRun: false,
        enabled: true,
      },
    ],
  };
  const network = transport(
    (operation) => (operation === "AstroliftServerInfo" ? null : { me }),
    (operation) => (operation === "AstroliftServerInfo" ? 250 : 5)
  );
  const { result } = renderHook(() => useModulesCard(), { wrapper: network.wrapper });
  await waitFor(() => expect(result.current.items[0].enabled).toBe(true));
  expect(result.current.loading).toBe(true);
  expect(result.current.error).toBeNull();
  await waitFor(() => expect(result.current.error?.message).toBe("Permission denied"));
  expect(result.current.loading).toBe(false);
});
