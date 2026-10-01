"use client";

import { useTranslations } from "next-intl";

import { useQuery } from "@apollo/client/react";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { LIST_WORKFLOW_DEFINITION_RUNS } from "@/graphql/workflows/tiered.queries";
import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";

import { agentRunHref, runReason, workflowRunHref, workflowRunReason } from "./apps-agents-model";
import type { FailedRunItem, FailedRunsPanelViewProps } from "./FailedRunsPanel";
import {
  combineReads,
  type HomeAgentTask,
  type HomeRead,
  readState,
  RUNS_POLL_MS,
  useHomeAgentRuns,
  usePanelAccess,
} from "./home-reads";

const TOP = 5;

interface WorkflowRunsResp {
  workflowDefinitionRuns: WorkflowDefinitionRun[];
}

export function agentFailedItem(t: HomeAgentTask, missing?: string): FailedRunItem {
  return {
    key: `agent:${t.id}`,
    kind: "agent",
    subject: t.agentName || t.agentSlug,
    id: t.id,
    reason: runReason(t.failureMessage, missing),
    at: t.finishedAt ?? t.startedAt ?? t.createdAt,
    href: agentRunHref(t.id),
  };
}

export function workflowFailedItem(
  r: WorkflowDefinitionRun,
  t?: ReturnType<typeof useTranslations<"home">>
): FailedRunItem {
  return {
    key: `workflow:${r.guid}`,
    kind: "workflow",
    subject: r.definitionName || r.definitionSlug,
    id: r.guid,
    reason: t
      ? r.currentStageOrder == null
        ? t(r.status === "timed_out" ? "copy.timedOut" : "status.failed")
        : t(r.status === "timed_out" ? "operations.timedOutAt" : "operations.failedAt", {
            stage: t("operations.stage", {
              order: r.currentStageOrder,
              role: r.currentStageRole ? ` (${r.currentStageRole})` : "",
            }),
          })
      : workflowRunReason(r),
    at: r.endedAt ?? r.startedAt ?? "",
    href: workflowRunHref(r.definitionSlug, r.guid),
  };
}

/**
 * Failed runs' data: the five newest failed agent runs (the read Failing
 * shares) and, when the viewer may see Workflows, the five newest failed
 * workflow runs. The count is the agent total plus the workflow runs,
 * unless five came back and there may be more.
 */
export function useFailedRuns(): Omit<FailedRunsPanelViewProps, "panel"> {
  const t = useTranslations("home");
  const { canView } = usePanelAccess();
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const workflowsOn = canView("workflows");
  const agents = useHomeAgentRuns("failed");
  const workflows = useQuery<WorkflowRunsResp>(LIST_WORKFLOW_DEFINITION_RUNS, {
    variables: { orgId: orgId || null, projectId: null, status: "failed", limit: TOP },
    fetchPolicy: "cache-and-network",
    pollInterval: RUNS_POLL_MS,
    skip: !orgId || !workflowsOn,
  });
  const workflowRuns = workflowsOn ? (workflows.data?.workflowDefinitionRuns ?? []) : [];
  const items = [
    ...agents.runs.map((r) => agentFailedItem(r, t("copy.noRunReason"))),
    ...workflowRuns.map((r) => workflowFailedItem(r, t)),
  ];

  const sources: HomeRead[] = [agents];
  if (workflowsOn) sources.push(readState(workflows, Boolean(workflows.data)));
  const count =
    agents.total === null || workflowRuns.length >= TOP ? null : agents.total + workflowRuns.length;
  return { items, count, ...combineReads(sources, items.length > 0) };
}
