import { MockedProvider } from "@apollo/client/testing/react";
import type { MockedResponse } from "@apollo/client/testing";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  GET_CONFIGURED_WORKFLOW,
  GET_TIERED_WORKFLOW_DEFINITION,
} from "@/graphql/workflows/tiered.queries";
import type { ConfiguredWorkflowWithRuns } from "@/graphql/workflows/tiered.types";

import { useFramedWorkflow } from "./framed-workflow";
import { WorkflowFrameContainer } from "./workflow-frame";

vi.mock("next/navigation", () => ({
  usePathname: () => "/workflows/nightly-sync/runs/run-1",
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1" } }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useModules: () => ({
    canView: () => true,
    canCreate: () => false,
    canManage: () => false,
    canRun: () => false,
  }),
}));

const workflow: ConfiguredWorkflowWithRuns = {
  guid: "workflow-1",
  name: "Nightly sync",
  slug: "nightly-sync",
  description: "",
  triggerKind: "manual",
  scheduleCron: null,
  isEnabled: true,
  inputs: {},
  stageBindings: {},
  organizationGuid: "org-1",
  definitionSlug: "sync-definition",
  definitionName: "Sync",
  patternKind: "sequential",
  runCount: 0,
  createdAt: "2026-09-01T12:00:00Z",
  runs: [],
};
const request = { query: GET_CONFIGURED_WORKFLOW, variables: { slug: workflow.slug, orgId: null } };
function RunBody() {
  const framed = useFramedWorkflow();
  return (
    <h1>Run of {framed.kind === "configured" ? framed.workflow.name : framed.definition.name}</h1>
  );
}
function setup(responses: MockedResponse[]) {
  return render(
    <MockedProvider mocks={responses}>
      <WorkflowFrameContainer slug={workflow.slug}>
        <RunBody />
      </WorkflowFrameContainer>
    </MockedProvider>
  );
}

describe("cold workflow run links", () => {
  it("waits for the actual Apollo workflow before rendering its context consumer", async () => {
    setup([
      { request, delay: 100, result: { data: { workflow } } },
      {
        request: {
          query: GET_TIERED_WORKFLOW_DEFINITION,
          variables: { slug: workflow.definitionSlug, orgId: "org-1" },
        },
        result: { data: { workflowDefinition: null } },
      },
    ]);
    expect(screen.queryByRole("heading", { name: "Run of Nightly sync" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Run of Nightly sync" })).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Workflow sections" })).not.toBeInTheDocument();
  });
  it("shows a workflow load failure instead of mounting a run without context", async () => {
    setup([
      { request, delay: 30, error: new Error("Workflow service unavailable") },
      {
        request: {
          query: GET_TIERED_WORKFLOW_DEFINITION,
          variables: { slug: workflow.slug, orgId: "org-1" },
        },
        result: { data: { workflowDefinition: null } },
      },
    ]);
    expect(await screen.findByRole("alert")).toHaveTextContent("Workflow service unavailable");
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByRole("heading", { name: /Run of/ })).not.toBeInTheDocument();
  });
});
