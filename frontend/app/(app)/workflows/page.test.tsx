import { renderWithIntl as render } from "@/test/render-with-intl";
import type { ReactNode } from "react";

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { workflowsFormerTabTarget } from "@/components/screens/workflows/list/workflows-list";

import { WorkflowsClient } from "./workflows-client";

const state = vi.hoisted(() => ({
  query: "",
  canAudit: false,
  runDefinition: vi.fn(),
  push: vi.fn(),
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
  useRouter: () => ({ replace: state.replace, push: state.push }),
  usePathname: () => "/workflows",
  useSearchParams: () => new URLSearchParams(state.query),
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({
    data: { workflowsPage: { items: [], nextCursor: null, totalCount: 0 } },
    loading: false,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1" } }),
}));

vi.mock("@/graphql/workflows/tiered.hooks", () => ({
  useWorkflowDefinitions: () => ({
    definitions: state.definitions,
    loading: false,
    refetch: vi.fn(),
  }),
  useDeleteDefinition: () => [vi.fn()],
  useCloneDefinition: () => [vi.fn()],
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

function openMenu(name: string) {
  fireEvent.pointerDown(screen.getByRole("button", { name }), { button: 0, ctrlKey: false });
}

describe("Workflows list", () => {
  beforeEach(() => {
    state.query = "";
    state.canAudit = false;
    state.definitionRuns = [];
    state.push.mockReset();
    state.replace.mockReset();
    state.runDefinition.mockReset();
    state.runDefinition.mockResolvedValue({
      data: { runWorkflowDefinition: { ok: true, errors: [] } },
    });
  });

  it("lists an imported definition directly and starts one without a configured wrapper", async () => {
    render(<WorkflowsClient />);

    expect(screen.getByRole("link", { name: /EMR Triage/ })).toHaveAttribute(
      "href",
      "/workflows/emr-triage"
    );
    expect(screen.getByText("steadymd/smd-agents/workflows/emr-triage.toml")).toBeInTheDocument();

    openMenu("Workflows: row actions");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Run now" }));

    await waitFor(() =>
      expect(state.runDefinition).toHaveBeenCalledWith({
        variables: { workflowSlug: "emr-triage", triggerPayload: null },
      })
    );
    await waitFor(() => expect(state.push).toHaveBeenCalledWith("/workflows/emr-triage/runs"));
  });

  it("shows a definition's run state without audit access, and keeps raw Temporal behind it", async () => {
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

    render(<WorkflowsClient />);

    expect(screen.getByText("Running")).toBeInTheDocument();
    openMenu("More workflow actions");
    expect(await screen.findByRole("menuitem", { name: "Workflow runs" })).toHaveAttribute(
      "href",
      "/tasks?kind=workflow"
    );
    expect(screen.queryByRole("menuitem", { name: "Platform instances" })).not.toBeInTheDocument();
  });

  it("offers the platform instances to audit readers", async () => {
    state.canAudit = true;
    render(<WorkflowsClient />);
    openMenu("More workflow actions");
    expect(await screen.findByRole("menuitem", { name: "Platform instances" })).toHaveAttribute(
      "href",
      "/workflows/instances"
    );
  });

  it("sends the old tabs where they went", () => {
    expect(workflowsFormerTabTarget({})).toBeNull();
    expect(workflowsFormerTabTarget({ tab: "running" })).toBe("/tasks?view=running&kind=workflow");
    expect(workflowsFormerTabTarget({ tab: "history" })).toBe("/tasks?kind=workflow");
    expect(workflowsFormerTabTarget({ tab: "definitions" })).toBe("/workflows?view=templates");
    expect(workflowsFormerTabTarget({ tab: "nonsense" })).toBe("/workflows");
  });
});
