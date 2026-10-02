import { renderWithIntl as render } from "@/test/render-with-intl";
import type { ReactNode } from "react";

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { workflowsFormerTabTarget } from "@/components/screens/workflows/list/workflows-list";
import { GET_ME } from "@/graphql/user/user.queries";
import { REVIEW_WORKFLOW_START } from "@/graphql/reviewed-starts/reviewed.queries";
import { START_REVIEWED_WORKFLOW } from "@/graphql/reviewed-starts/reviewed.mutations";

import { WorkflowsClient } from "./workflows-client";

const state = vi.hoisted(() => ({
  query: "",
  canAudit: false,
  runDefinition: vi.fn(),
  review: vi.fn(),
  start: vi.fn(),
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
  useApolloClient: () => ({ query: state.review, mutate: state.start }),
  useQuery: (query: unknown) => ({
    data:
      query === GET_ME
        ? { me: { id: "actor-1" } }
        : { workflowsPage: { items: [], nextCursor: null, totalCount: 0 } },
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
    sessionStorage.clear();
    state.query = "";
    state.canAudit = false;
    state.definitionRuns = [];
    state.push.mockReset();
    state.replace.mockReset();
    state.runDefinition.mockReset();
    state.runDefinition.mockResolvedValue({
      data: { runWorkflowDefinition: { ok: true, errors: [] } },
    });
    state.review.mockReset().mockResolvedValue({
      data: {
        workflowDefinitionById: {
          guid: "workflow-1",
          revision: "reviewed-revision",
          definition: state.definitions[0],
          inputContract: {
            schema: { type: "object", additionalProperties: false },
            digest: "reviewed-schema",
            supported: true,
            error: "",
            fields: [],
            acceptsInputs: false,
            supportsSimpleForm: true,
          },
        },
      },
    });
    state.start.mockReset().mockResolvedValue({
      data: {
        startWorkflowDefinition: {
          ok: true,
          errors: [],
          data: {
            id: "execution-1",
            requestId: "request-1",
            dispatchStatus: "submitted",
            temporalWorkflowId: "reviewed-engine",
            temporalRunId: "reviewed-engine-run",
          },
        },
      },
    });
  });

  it("lists an imported definition directly and starts one without a configured wrapper", async () => {
    render(<WorkflowsClient />);

    expect(screen.getByRole("link", { name: /EMR Triage/ })).toHaveAttribute(
      "href",
      "/workflows/emr-triage?definitionId=workflow-1"
    );
    expect(screen.getByText("steadymd/smd-agents/workflows/emr-triage.toml")).toBeInTheDocument();

    openMenu("Workflows: row actions");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Run now" }));

    await waitFor(() =>
      expect(state.review).toHaveBeenCalledWith({
        query: REVIEW_WORKFLOW_START,
        variables: { id: "workflow-1" },
        fetchPolicy: "no-cache",
      })
    );
    expect(state.start).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Start reviewed run" }));
    await waitFor(() =>
      expect(state.start).toHaveBeenCalledWith({
        mutation: START_REVIEWED_WORKFLOW,
        variables: {
          input: {
            definitionId: "workflow-1",
            expectedRevision: "reviewed-revision",
            expectedInputSchemaDigest: "reviewed-schema",
            requestId: expect.any(String),
            inputs: {},
            confirmed: true,
          },
        },
      })
    );
    expect(
      await screen.findByText(
        "The engine accepted this execution. This does not mean the run has finished."
      )
    ).toBeVisible();
    expect(state.runDefinition).not.toHaveBeenCalled();
    expect(state.push).not.toHaveBeenCalled();
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
