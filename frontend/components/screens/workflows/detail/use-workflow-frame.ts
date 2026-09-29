"use client";

import { usePathname, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useRunWorkflow,
  useRunWorkflowDefinition,
  useTieredWorkflow,
  useWorkflowDefinition,
  useWorkflowDefinitionRuns,
  useWorkflowsEntitlement,
  useWorkflowStageExecutions,
} from "@/graphql/workflows/tiered.hooks";
import type {
  ConfiguredWorkflowWithRuns,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";

import {
  configuredSubject,
  definitionSubject,
  isFailedState,
  type WorkflowFrameSubject,
} from "./workflow-frame-subject";
import { workflowRunHref } from "./workflow-run-model";
import { resolveWorkflowTab, workflowTabHref } from "./workflow-tabs-model";
import type { WorkflowFrameProps } from "./WorkflowFrame";

/** What every tab under the frame reads: the one workflow, of whichever kind. */
export type FramedWorkflow =
  | { kind: "configured"; workflow: ConfiguredWorkflowWithRuns; refetch: () => void }
  | { kind: "definition"; definition: WorkflowDefinitionSummary };

/** A definition the viewer may delete: org-owned, not reconciled from a repository. */
export function definitionDeletable(definition: WorkflowDefinitionSummary): boolean {
  return !definition.isGlobal && Boolean(definition.organizationGuid) && !definition.sourceRepo;
}

/**
 * The data half of WorkflowFrame, and the workflow every tab reads (the
 * frame hands it down once found).
 *
 * A `/workflows/[slug]` is a configured workflow when one has the slug, else a
 * definition opened directly, as the pillar pages resolved it. The definition
 * is read with the active org, the same variables the Builder tab's stage
 * builder uses, so Apollo serves it once: for a configured workflow it is the
 * definition behind it (stage count, project), for a definition it is the
 * page. A configured workflow's last run comes with it; a definition's runs are
 * the same `workflowDefinitionRuns` query the Runs tab polls, read here without
 * a poll. The failed run's reason is its stage executions' error, read only
 * when the last run failed, from the query the run graph polls. Run is the
 * workflows module's run grant, as before.
 */
export function useWorkflowFrame(slug: string): {
  frame: Omit<WorkflowFrameProps, "children">;
  framed: FramedWorkflow | null;
} {
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const { org } = useActiveOrg();
  const orgId = org?.id ?? null;
  const entitlement = useWorkflowsEntitlement();

  const configuredQ = useTieredWorkflow(slug);
  const workflow = configuredQ.error ? null : configuredQ.workflow;
  const configuredSettled = !(configuredQ.loading && !configuredQ.workflow);
  const definitionSlug = workflow?.definitionSlug ?? (configuredSettled ? slug : null);
  const definitionQ = useWorkflowDefinition(definitionSlug, orgId);
  const definition = workflow ? null : definitionQ.definition;

  const definitionRunsQ = useWorkflowDefinitionRuns({
    orgId: definition?.organizationGuid,
    projectId: definition?.projectGuid,
    limit: 100,
    skip: !definition,
  });

  const subject: WorkflowFrameSubject | null = workflow
    ? configuredSubject(workflow, definitionQ.definition)
    : definition
      ? definitionSubject(definition, definitionRunsQ.runs)
      : null;

  const lastRun = subject?.lastRun ?? null;
  const failing = Boolean(lastRun && !lastRun.live && isFailedState(lastRun.state));
  const executionsQ = useWorkflowStageExecutions({
    workflowId: failing ? (lastRun?.temporalWorkflowId ?? null) : null,
    runId: failing ? (lastRun?.temporalRunId ?? null) : null,
  });
  const reason =
    executionsQ.executions.find((x) => isFailedState(x.status) && x.errorMessage)?.errorMessage ??
    null;

  const [runConfigured, { loading: runningConfigured }] = useRunWorkflow();
  const [runDefinition, { loading: runningDefinition }] = useRunWorkflowDefinition();

  async function dispatch(): Promise<boolean> {
    if (workflow) {
      const { data } = await runConfigured({
        variables: { workflowId: workflow.guid, orgId: workflow.organizationGuid },
      });
      if (data?.runWorkflow?.ok) {
        toast.success("Run started", {
          description: data.runWorkflow.workflowRunId
            ? `Run ${data.runWorkflow.workflowRunId}`
            : workflow.name,
        });
        void configuredQ.refetch();
        return true;
      }
      const errors = data?.runWorkflow?.errors ?? [];
      if (errors.length > 0)
        for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
      else toast.error("Failed to start run");
      return false;
    }
    if (definition) {
      const { data } = await runDefinition({
        variables: { workflowSlug: definition.slug, triggerPayload: null },
      });
      if (data?.runWorkflowDefinition?.ok) {
        toast.success("Workflow started", {
          description: data.runWorkflowDefinition.temporalWorkflowId ?? definition.name,
        });
        return true;
      }
      toast.error(
        data?.runWorkflowDefinition?.errors?.[0]?.messages?.[0] ?? "Failed to start workflow"
      );
    }
    return false;
  }

  async function onRun(): Promise<boolean> {
    const started = await dispatch();
    if (started && resolveWorkflowTab(pathname, slug) !== "runs")
      router.push(workflowTabHref(slug, "runs"));
    return started;
  }

  function onCopyId() {
    if (!subject) return;
    void navigator.clipboard?.writeText(subject.id).then(
      () => toast.success("Workflow ID copied"),
      () => toast.error(subject.id)
    );
  }

  const refetchConfigured = configuredQ.refetch;
  const framed = React.useMemo<FramedWorkflow | null>(
    () =>
      workflow
        ? { kind: "configured", workflow, refetch: () => void refetchConfigured() }
        : definition
          ? { kind: "definition", definition }
          : null,
    [workflow, definition, refetchConfigured]
  );

  const loading =
    !configuredSettled || (!workflow && definitionQ.loading && !definitionQ.definition);

  return {
    framed,
    frame: {
      slug,
      pathname,
      workflow: subject,
      loading,
      error: !workflow && !definition && definitionQ.error ? definitionQ.error.message : null,
      onRetry: () => {
        void configuredQ.refetch();
        void definitionQ.refetch();
      },
      failedRun: failing && lastRun ? { href: workflowRunHref(slug, lastRun.guid), reason } : null,
      canRun: entitlement.canRun,
      canDelete:
        entitlement.canManage &&
        (workflow ? true : definition ? definitionDeletable(definition) : false),
      dispatching: runningConfigured || runningDefinition,
      onRun,
      onCopyId,
    },
  };
}
