import type { ReactNode } from "react";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WorkflowsPage from "./page";

const state = vi.hoisted(() => ({
  tab: "",
  canAudit: false,
  runDefinition: vi.fn(),
  replace: vi.fn(),
  definitions: [
    {
      guid: "workflow-1",
      name: "EMR Triage",
      slug: "emr-triage",
      description: "",
      patternKind: "chained",
      isEnabled: true,
      isGlobal: false,
      organizationGuid: "org-1",
      projectGuid: "project-1",
      projectSlug: "emr-bug-triage",
      projectTeamSlug: "engineering",
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
          agentGuid: "agent-1",
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
          agentGuid: "agent-2",
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
  ],
  definitionRuns: [] as Array<Record<string, unknown>>,
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: state.replace }),
  usePathname: () => "/workflows",
  useSearchParams: () => new URLSearchParams(state.tab ? `tab=${state.tab}` : ""),
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({ data: { workflows: [], astroliftWorkflowRuns: [] }, loading: false }),
}));

vi.mock("@/graphql/workflows/tiered.hooks", () => ({
  useWorkflowDefinitions: () => ({ definitions: state.definitions, loading: false }),
  useDeleteDefinition: () => [vi.fn()],
  useWorkflowsEntitlement: () => ({
    canCreate: true,
    canManage: true,
    canRun: true,
  }),
  useRunWorkflow: () => [vi.fn()],
  useRunWorkflowDefinition: () => [state.runDefinition],
  useUpdateConfiguredWorkflow: () => [vi.fn()],
  useDeleteConfiguredWorkflow: () => [vi.fn()],
  useWorkflowDefinitionRuns: () => ({
    runs: state.definitionRuns,
    loading: false,
    error: undefined,
  }),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ loading: false, can: () => state.canAudit }),
}));

vi.mock("@/hooks/use-confirm", () => ({ useConfirm: () => vi.fn() }));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/components/PageShell", () => ({
  PageShell: ({
    title,
    actions,
    children,
  }: {
    title: ReactNode;
    actions?: ReactNode;
    children: ReactNode;
  }) => (
    <main>
      <h1>{title}</h1>
      {actions}
      {children}
    </main>
  ),
}));

vi.mock("./instances-panel", () => ({
  WorkflowInstancesPanel: () => <div>Temporal instances panel</div>,
}));

describe("WorkflowsPage repository topology", () => {
  beforeEach(() => {
    state.tab = "";
    state.canAudit = false;
    state.definitionRuns = [];
    state.replace.mockReset();
    state.runDefinition.mockReset();
    state.runDefinition.mockResolvedValue({
      data: { runWorkflowDefinition: { ok: true, errors: [] } },
    });
  });

  it("renders imported definitions directly and starts one without a configured wrapper", async () => {
    render(<WorkflowsPage />);

    expect(screen.getByText("Repository workflows")).toBeInTheDocument();
    expect(
      screen.getByRole("application", { name: "Workflow stage topology" })
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() =>
      expect(state.runDefinition).toHaveBeenCalledWith({
        variables: { workflowSlug: "emr-triage", triggerPayload: null },
      })
    );
    expect(state.replace).toHaveBeenCalledWith("/workflows?tab=running", { scroll: false });
  });

  it("shows direct definition runs without requiring audit access to raw Temporal", () => {
    state.tab = "running";
    state.definitionRuns = [
      {
        guid: "run-1",
        definitionGuid: "workflow-1",
        definitionSlug: "emr-triage",
        definitionName: "EMR Triage",
        projectGuid: "project-1",
        projectSlug: "emr-bug-triage",
        status: "running",
        temporalWorkflowId: "WorkflowDefinitionRunWorkflow-92",
        temporalRunId: "temporal-run-1",
        currentStageOrder: 0,
        currentStageRole: "intake",
        startedAt: "2026-08-13T12:00:00Z",
        endedAt: null,
      },
    ];

    render(<WorkflowsPage />);

    expect(screen.getByText("Agent workflow runs")).toBeInTheDocument();
    expect(screen.getByText("Stage 1: intake")).toBeInTheDocument();
    expect(screen.queryByText("Temporal instances panel")).not.toBeInTheDocument();
  });
});
