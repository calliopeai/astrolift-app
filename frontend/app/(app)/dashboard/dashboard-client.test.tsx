import type { ReactNode } from "react";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardClient } from "./dashboard-client";

type DashboardState = {
  modules: Set<string>;
  permissions: Set<string>;
  calls: Array<{ operation: string; skip: boolean }>;
  agentTasks: Record<string, unknown>[];
  workflowRuns: Record<string, unknown>[];
};

const state = vi.hoisted<DashboardState>(() => ({
  modules: new Set(),
  permissions: new Set(),
  calls: [],
  agentTasks: [],
  workflowRuns: [],
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: (
    document: { definitions?: Array<{ kind: string; name?: { value: string } }> },
    options?: { skip?: boolean }
  ) => {
    const operation =
      document.definitions?.find((definition) => definition.kind === "OperationDefinition")?.name
        ?.value ?? "";
    state.calls.push({ operation, skip: options?.skip ?? false });
    const data: Record<string, unknown> = {
      ListTeams: { astroliftTeams: [] },
      ListProjects: { astroliftProjects: [] },
      ListAppHealthSummary: { astroliftAppHealthSummary: [] },
      GetDeploymentMetrics: { astroliftDeploymentMetrics: { inFlight: 0 } },
      GetCostForecast: null,
      ListAgentFleet: {
        agentFleet: [
          {
            id: "agent-1",
            slug: "emr-triage-intake",
            name: "EMR Triage Intake",
          },
        ],
      },
      ListAgentTasks: { agentTasks: state.agentTasks },
      ListTieredWorkflowDefinitions: {
        workflowDefinitions: [
          {
            guid: "workflow-1",
            slug: "emr-triage",
            sourceRepo: "steadymd/smd-agents",
            projectGuid: "project-1",
            isGlobal: false,
          },
        ],
      },
      ListWorkflowDefinitionRuns: { workflowDefinitionRuns: state.workflowRuns },
    };
    return { data: options?.skip ? undefined : data[operation], loading: false, error: undefined };
  },
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/components/PageShell", () => ({
  PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/components/KpiTile", () => ({
  KpiTile: ({ label, value }: { label: string; value?: ReactNode }) => (
    <div>
      {label}: {value}
    </div>
  ),
}));

vi.mock("./onboarding-host", () => ({ OnboardingHost: () => null }));
vi.mock("@/components/ActivityFeed", () => ({ ActivityFeed: () => <div>Activity feed</div> }));

vi.mock("@dnd-kit/core", () => ({
  DndContext: ({ children }: { children: ReactNode }) => <>{children}</>,
  KeyboardSensor: class {},
  PointerSensor: class {},
  closestCenter: vi.fn(),
  useSensor: vi.fn(() => ({})),
  useSensors: vi.fn(() => []),
}));

vi.mock("@dnd-kit/sortable", () => ({
  SortableContext: ({ children }: { children: ReactNode }) => <>{children}</>,
  arrayMove: (rows: unknown[]) => rows,
  rectSortingStrategy: vi.fn(),
  sortableKeyboardCoordinates: vi.fn(),
  useSortable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: vi.fn(),
    setActivatorNodeRef: vi.fn(),
    transform: null,
    transition: undefined,
    isDragging: false,
  }),
}));

vi.mock("@dnd-kit/utilities", () => ({ CSS: { Transform: { toString: () => undefined } } }));

vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1", slug: "steadymd" } }),
}));

vi.mock("@/graphql/user/user.hooks", () => ({
  useModules: () => ({
    loading: false,
    canView: (key: string) => state.modules.has(key),
  }),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    loading: false,
    can: (permission: string) => state.permissions.has(permission),
  }),
}));

describe("DashboardClient role-scoped operational state", () => {
  beforeEach(() => {
    state.modules = new Set();
    state.permissions = new Set();
    state.calls = [];
    state.agentTasks = [];
    state.workflowRuns = [];
  });

  it("shows active agents without querying or revealing inaccessible modules", () => {
    state.modules.add("agents");
    state.agentTasks = [
      {
        id: "task-1",
        agentSlug: "emr-triage-intake",
        agentName: "EMR Triage Intake",
        projectSlug: "emr-bug-triage",
        status: "running",
        createdAt: "2026-08-13T12:00:00Z",
        startedAt: "2026-08-13T12:01:00Z",
        finishedAt: null,
      },
    ];

    render(<DashboardClient />);

    expect(screen.getByText("Operational state")).toBeInTheDocument();
    expect(screen.getByText("Agents running now")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "EMR Triage Intake" })).toHaveAttribute(
      "href",
      "/agents/runs/task-1"
    );
    expect(screen.queryByText("Workflows running now")).not.toBeInTheDocument();
    expect(screen.queryByText("Activity feed")).not.toBeInTheDocument();
    expect(state.calls.find((call) => call.operation === "ListAgentTasks")?.skip).toBe(false);
    expect(state.calls.find((call) => call.operation === "ListAppHealthSummary")?.skip).toBe(true);
    expect(
      state.calls.find((call) => call.operation === "ListTieredWorkflowDefinitions")?.skip
    ).toBe(true);
  });

  it("shows workflow execution state and audit activity only to an entitled viewer", () => {
    state.modules.add("workflows");
    state.permissions.add("audit_log.read");
    state.workflowRuns = [
      {
        guid: "run-1",
        definitionGuid: "workflow-1",
        definitionSlug: "emr-triage",
        definitionName: "EMR Triage",
        projectSlug: "emr-bug-triage",
        status: "running",
        currentStageOrder: 1,
        currentStageRole: "decide",
        startedAt: "2026-08-13T12:00:00Z",
        endedAt: null,
      },
    ];

    render(<DashboardClient />);

    expect(screen.getByText("Workflows running now")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "EMR Triage" })).toHaveAttribute(
      "href",
      "/workflows/emr-triage/builder"
    );
    expect(screen.getByText("emr-bug-triage · stage 2 decide")).toBeInTheDocument();
    expect(screen.getByText("Activity feed")).toBeInTheDocument();
    expect(state.calls.find((call) => call.operation === "ListWorkflowDefinitionRuns")?.skip).toBe(
      false
    );
    expect(state.calls.find((call) => call.operation === "ListAgentTasks")?.skip).toBe(true);
  });
});
