import type { ReactNode } from "react";

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectDetailClient } from "./project-detail-client";

type QueryState = {
  apps: Record<string, unknown>[];
  agents: Record<string, unknown>[];
  live: Record<string, unknown>[];
  workflows: Record<string, unknown>[];
  workflowRuns: Record<string, unknown>[];
  appsError?: Error;
  agentsError?: Error;
  canViewAgents: boolean;
  canCreateAgent: boolean;
  canViewWorkflows: boolean;
  calls: Array<{ operation: string; variables: Record<string, unknown>; skip: boolean }>;
};

const state = vi.hoisted<QueryState>(() => ({
  apps: [],
  agents: [],
  live: [],
  workflows: [],
  workflowRuns: [],
  canViewAgents: true,
  canCreateAgent: true,
  canViewWorkflows: true,
  calls: [],
}));

const project = {
  id: "project-1",
  slug: "emr-bug-triage",
  name: "EMR Bug Triage",
  organization: { id: "org-1", slug: "steadymd", name: "SteadyMD" },
  team: { id: "team-1", slug: "engineering", name: "Engineering" },
  createdAt: "2026-08-01T12:00:00Z",
  updatedAt: "2026-08-01T12:00:00Z",
  deletedAt: null,
};

vi.mock("@apollo/client/react", () => ({
  useMutation: () => [vi.fn(), { loading: false }],
  useQuery: (
    document: { definitions?: Array<{ kind: string; name?: { value: string } }> },
    options?: { variables?: Record<string, unknown>; skip?: boolean }
  ) => {
    const operation =
      document.definitions?.find((definition) => definition.kind === "OperationDefinition")?.name
        ?.value ?? "";
    state.calls.push({
      operation,
      variables: options?.variables ?? {},
      skip: options?.skip ?? false,
    });
    const data: Record<string, unknown> = {
      ListProjects: { astroliftProjects: [project] },
      ListApps: { astroliftApps: state.apps },
      ListMembers: { astroliftMembers: [] },
      ListAgentWorkloads: { agentWorkloads: state.agents },
      ListAgentLiveStatus: { agentLiveStatus: state.live },
      ListTieredWorkflowDefinitions: { workflowDefinitions: state.workflows },
      ListWorkflowDefinitionRuns: { workflowDefinitionRuns: state.workflowRuns },
    };
    return {
      data: data[operation],
      loading: false,
      error:
        operation === "ListApps"
          ? state.appsError
          : operation === "ListAgentWorkloads" || operation === "ListAgentLiveStatus"
            ? state.agentsError
            : undefined,
      refetch: vi.fn(),
    };
  },
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/components/Can", () => ({
  Can: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/PageShell", () => ({
  PageShell: ({
    title,
    description,
    actions,
    children,
  }: {
    title: ReactNode;
    description?: ReactNode;
    actions?: ReactNode;
    children?: ReactNode;
  }) => (
    <main>
      <header>
        {title}
        {description}
        {actions}
      </header>
      {children}
    </main>
  ),
}));

vi.mock("@/components/ConfirmDialog", () => ({
  ConfirmDialog: () => null,
}));

vi.mock("@/graphql/user/user.hooks", () => ({
  useModules: () => ({
    loading: false,
    canView: (key: string) =>
      key === "agents" ? state.canViewAgents : key === "workflows" ? state.canViewWorkflows : true,
    canCreate: (key: string) => key !== "agents" || state.canCreateAgent,
  }),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    loading: false,
    can: (permission: string) => permission === "org.manage_members",
  }),
}));

vi.mock("@/lib/i18n/formatters", () => ({
  useFormatters: () => ({
    formatDate: () => "Aug 1, 2026",
    formatDateTime: () => "Aug 1, 2026 at 12:00 PM",
  }),
}));

function agent(slug: string, overrides: Record<string, unknown> = {}) {
  return {
    id: `workload-${slug}`,
    name: slug
      .split("-")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" "),
    slug,
    appSlug: slug,
    projectSlug: project.slug,
    sourceRepo: "steadymd/smd-agents",
    sourceUrl: "https://github.com/steadymd/smd-agents",
    runFamily: "task",
    runMode: "once",
    runPaused: false,
    runCronExpression: "",
    lastRunStatus: "completed",
    lastRunAt: "2026-08-13T14:00:00Z",
    runningCount: 0,
    ...overrides,
  };
}

describe("ProjectDetailClient workload-aware overview", () => {
  beforeEach(() => {
    state.apps = [];
    state.agents = [];
    state.live = [];
    state.workflows = [];
    state.workflowRuns = [];
    state.appsError = undefined;
    state.agentsError = undefined;
    state.canViewAgents = true;
    state.canCreateAgent = true;
    state.canViewWorkflows = true;
    state.calls = [];
  });

  it("renders an agent-only project as an agent operations overview", () => {
    state.agents = [
      agent("emr-triage-intake"),
      agent("emr-triage-research"),
      agent("emr-triage-decide"),
      agent("emr-triage-review"),
      agent("emr-bug-triage"),
    ];
    state.live = [
      {
        workloadId: "workload-emr-triage-intake",
        workloadSlug: "emr-triage-intake",
        appSlug: "emr-triage-intake",
        runFamily: "task",
        runMode: "once",
        isPaused: false,
        isIdle: false,
        runningCount: 1,
        lastRunStatus: "running",
        lastRunAt: "2026-08-13T15:00:00Z",
        nextScheduledAt: null,
      },
    ];

    render(<ProjectDetailClient slug={project.slug} />);

    expect(screen.getByText("Agent project")).toBeInTheDocument();
    expect(screen.getByText("Agents in this project")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Agents in this project" })).toBeInTheDocument();
    expect(screen.getByText("Registered agents")).toBeInTheDocument();
    expect(screen.getByText("Active agent runs")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /project resources/i })).toHaveAttribute(
      "href",
      "/projects/emr-bug-triage/resources"
    );
    expect(screen.getByText("1 running")).toBeInTheDocument();
    expect(screen.queryByText("Registered apps")).not.toBeInTheDocument();
    expect(screen.queryByText("Active deployments")).not.toBeInTheDocument();
    expect(screen.queryByText("No apps in this project")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /new app/i })).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /register agent repo/i })[0]).toHaveAttribute(
      "href",
      "/agents/new"
    );
    expect(screen.getByRole("link", { name: /agent activity/i })).toHaveAttribute(
      "href",
      "/agents?project=emr-bug-triage"
    );

    const agentQuery = state.calls.find((call) => call.operation === "ListAgentWorkloads");
    expect(agentQuery?.variables).toEqual({ orgId: "org-1", projectSlug: project.slug });
  });

  it("shows both workload sections for a mixed project", () => {
    state.agents = [agent("triage-agent")];
    state.apps = [
      {
        id: "app-1",
        slug: "triage-api",
        name: "Triage API",
        projectSlug: project.slug,
        provisioningStatus: "ready",
        sourceRepo: "steadymd/triage-api",
        createdAt: "2026-08-01T12:00:00Z",
      },
    ];

    render(<ProjectDetailClient slug={project.slug} />);

    expect(screen.getByText("Apps + agents")).toBeInTheDocument();
    expect(screen.getByText("Agents in this project")).toBeInTheDocument();
    expect(screen.getByText("Apps in this project")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /new app/i })).toHaveAttribute("href", "/apps/new");
    expect(screen.getByRole("link", { name: /project resources/i })).toHaveAttribute(
      "href",
      "/projects/emr-bug-triage/resources"
    );
  });

  it("renders project workflows as topology and marks standalone agents", () => {
    state.agents = [
      agent("emr-triage-intake"),
      agent("emr-triage-decide"),
      agent("emr-bug-triage"),
    ];
    state.workflows = [
      {
        guid: "workflow-1",
        name: "EMR triage — code research and review",
        slug: "emr-triage",
        description: "",
        patternKind: "chained",
        isEnabled: true,
        isGlobal: false,
        organizationGuid: "org-1",
        projectGuid: project.id,
        projectSlug: project.slug,
        projectTeamSlug: project.team.slug,
        sourceRepo: "steadymd/smd-agents",
        sourcePath: "workflows/emr-triage.toml",
        sourceRef: "main",
        stageCount: 2,
        createdAt: "2026-08-13T12:00:00Z",
        stages: [
          {
            guid: "stage-1",
            order: 0,
            kind: "agent_dispatch",
            role: "intake",
            agentRef: "emr-triage-intake",
            agentGuid: "workload-emr-triage-intake",
            agentName: "EMR Triage Intake",
            agentSlug: "emr-triage-intake",
            environmentSpecSlug: "emr-triage-intake",
            resolvedModel: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            hasPrompt: true,
            outputKey: "evidence",
            skillRefs: ["jira-search"],
            fanOutCount: null,
            fanOutDynamic: false,
            onFailure: "fail",
            timeoutSeconds: 300,
          },
          {
            guid: "stage-2",
            order: 1,
            kind: "agent_dispatch",
            role: "decide",
            agentRef: "emr-triage-decide",
            agentGuid: "workload-emr-triage-decide",
            agentName: "EMR Triage Decide",
            agentSlug: "emr-triage-decide",
            environmentSpecSlug: "emr-triage-decide",
            resolvedModel: "us.anthropic.claude-opus-4-8",
            hasPrompt: true,
            outputKey: "decision",
            skillRefs: [],
            fanOutCount: null,
            fanOutDynamic: false,
            onFailure: "fail",
            timeoutSeconds: 300,
          },
        ],
      },
    ];

    render(<ProjectDetailClient slug={project.slug} />);

    expect(screen.getByText("Agent workflow project")).toBeInTheDocument();
    expect(screen.getByText("Workflow topology")).toBeInTheDocument();
    expect(
      screen.getByRole("application", { name: "Project workflow topology" })
    ).toBeInTheDocument();
    expect(screen.getByText("Standalone")).toBeInTheDocument();
    expect(screen.getAllByText("emr-triage").length).toBeGreaterThan(0);
    const workflowNode = screen.getByTitle("Open EMR triage — code research and review");
    expect(workflowNode).toHaveClass("relative", "flex", "h-16", "w-56", "overflow-visible");
    expect(within(workflowNode).getByText("EMR triage — code research and review")).toHaveClass(
      "min-w-0",
      "truncate"
    );
    const workflowQuery = state.calls.find(
      (call) => call.operation === "ListTieredWorkflowDefinitions"
    );
    expect(workflowQuery?.variables).toEqual({ orgId: "org-1", projectId: "project-1" });
  });

  it("uses a neutral workload empty state when the project is truly empty", () => {
    render(<ProjectDetailClient slug={project.slug} />);

    expect(screen.getByText("Registered workloads")).toBeInTheDocument();
    expect(screen.getByText("No workloads in this project")).toBeInTheDocument();
    expect(screen.queryByText("Registered apps")).not.toBeInTheDocument();
    expect(screen.queryByText("No apps in this project")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /project resources/i })).toHaveAttribute(
      "href",
      "/projects/emr-bug-triage/resources"
    );

    const emptyCard = screen
      .getByText("No workloads in this project")
      .closest<HTMLElement>('[data-slot="card"]');
    expect(emptyCard).not.toBeNull();
    expect(within(emptyCard!).getByRole("link", { name: /register agent repo/i })).toHaveAttribute(
      "href",
      "/agents/new"
    );
    expect(within(emptyCard!).getByRole("link", { name: /register app/i })).toHaveAttribute(
      "href",
      "/apps/new"
    );
  });

  it("does not misclassify partial data as an agent-only project", () => {
    state.agents = [agent("triage-agent")];
    state.appsError = new Error("apps unavailable");

    render(<ProjectDetailClient slug={project.slug} />);

    expect(screen.getByText("Agents in this project")).toBeInTheDocument();
    expect(screen.getByText("Workload summary incomplete")).toBeInTheDocument();
    expect(screen.queryByText("Agent project")).not.toBeInTheDocument();
    expect(screen.queryByText("No workloads in this project")).not.toBeInTheDocument();
  });

  it("does not query or reveal agents when the module is unavailable", () => {
    state.agents = [agent("hidden-agent")];
    state.canViewAgents = false;
    state.canCreateAgent = false;

    render(<ProjectDetailClient slug={project.slug} />);

    expect(screen.getByText("No visible workloads")).toBeInTheDocument();
    expect(screen.queryByText("Hidden Agent")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /register agent repo/i })).not.toBeInTheDocument();
    expect(state.calls.find((call) => call.operation === "ListAgentWorkloads")?.skip).toBeTruthy();
  });

  it("does not query or reveal workflows when the module is unavailable", () => {
    state.agents = [agent("triage-agent")];
    state.workflows = [
      {
        guid: "hidden-workflow",
        slug: "hidden-workflow",
        name: "Hidden workflow",
        stages: [],
      },
    ];
    state.canViewWorkflows = false;

    render(<ProjectDetailClient slug={project.slug} />);

    expect(screen.queryByText("Hidden workflow")).not.toBeInTheDocument();
    expect(
      state.calls.find((call) => call.operation === "ListTieredWorkflowDefinitions")?.skip
    ).toBeTruthy();
  });
});
