/**
 * A workflow's Runs tab (spec 44 §5.1, §5.2): the shared Runs list's row
 * shape, filters and cursor paging, narrowed to one workflow. An agent's
 * Runs tab is the same idea (`AGENT_RUNS_LIST`), so this is that list with
 * the workflow's own list id and Mine note. Pure.
 */
import { standardViews, type ListDefinition } from "@/components/list/list-state";
import { AGENT_RUNS_LIST, type RunRow } from "@/components/screens/tasks/runs-list";
import type { TieredWorkflowRun } from "@/graphql/workflows/tiered.types";

import { configuredRunOutcome, workflowRunHref } from "./workflow-run-model";

/** All · Mine · Running · Failed · Waiting, status and since chips, newest first by cursor. */
export const WORKFLOW_RUNS_LIST: ListDefinition = {
  ...AGENT_RUNS_LIST,
  id: "workflows.workflow-runs",
  views: standardViews(
    { startedBy: "me" },
    AGENT_RUNS_LIST.views.filter((v) => v.key !== "all" && v.key !== "mine"),
    {
      mineNote:
        "Workflow runs don't record who started them yet, so Mine stays empty until they do.",
    }
  ),
};

function seconds(from: string | null | undefined, to: string | null | undefined): number | null {
  if (!from || !to) return null;
  const d = (Date.parse(to) - Date.parse(from)) / 1000;
  return Number.isFinite(d) && d >= 0 ? Math.round(d) : null;
}

/** A configured workflow's run as a Runs row, opening on the workflow's own run page. */
export function fromConfiguredRun(
  run: TieredWorkflowRun,
  workflow: { slug: string; name: string }
): RunRow {
  const outcome = configuredRunOutcome(run);
  const live = outcome === "running" || outcome === "waiting";
  return {
    key: `workflow:${run.guid}`,
    kind: "workflow",
    id: run.guid,
    subject: workflow.name,
    agent: "",
    workflow: workflow.slug,
    project: "",
    app: "",
    trigger: "",
    startedBy: "",
    startedByMe: false,
    at: run.startedAt,
    durationSeconds: seconds(run.startedAt, run.completedAt),
    status: run.currentState,
    outcome,
    href: workflowRunHref(workflow.slug, run.guid),
    cancel:
      live && run.temporalWorkflowId
        ? { kind: "workflow", workflowId: run.temporalWorkflowId }
        : null,
    watchHref: null,
  };
}
