"use client";

import * as React from "react";
import { toast } from "sonner";

import { downloadText, logText, useNow } from "@/components/screens/deployments/run-support";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  usePendingHumanGates,
  useWorkflowDefinition,
  useWorkflowDefinitionRun,
  useWorkflowRuns,
  useWorkflowsEntitlement,
  useWorkflowStageExecutions,
} from "@/graphql/workflows/tiered.hooks";
import {
  useCancelWorkflowInstance,
  useWorkflowInstanceDetail,
} from "@/graphql/workflows/workflows.hooks";
import { useMotion } from "@/lib/viz-prefs";

import { useGateReview } from "./use-gate-review";
import type { FramedWorkflow } from "./use-workflow-frame";
import {
  configuredRunSubject,
  definitionRunSubject,
  runLogLines,
  type WorkflowRunSubject,
} from "./workflow-run-model";
import type { WorkflowRunScreenProps } from "./WorkflowRunScreen";

/** Inside the 3 to 5 second live window (#1090); nothing polls once the run settles. */
const LIVE_POLL_MS = 4000;
const GATES_POLL_MS = 10_000;

/**
 * One run's page, the data half of WorkflowRunScreen. A configured
 * workflow's run comes from the `workflowRuns` list its Runs tab reads; a
 * definition's run is read by guid (`workflowDefinitionRun`, #2155), so a
 * run past the newest window of the definition's runs still opens. The
 * plan is the definition topology the frame already read. The stage executions and the engine history are the run's
 * own, polled while it is live. Whether a waiting gate waits on the viewer
 * is `pendingHumanGates`, the gates the server says the viewer may decide.
 */
export function useWorkflowRunPage(
  framed: FramedWorkflow,
  slug: string,
  runId: string
): WorkflowRunScreenProps {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? null;
  const entitlement = useWorkflowsEntitlement();
  const motion = useMotion();
  const configured = framed.kind === "configured" ? framed.workflow : null;
  const definition = framed.kind === "definition" ? framed.definition : null;

  const configuredQ = useWorkflowRuns(
    configured?.guid ?? null,
    configured?.organizationGuid ?? null
  );
  const definitionRunQ = useWorkflowDefinitionRun({
    guid: runId,
    orgId: definition?.organizationGuid,
    skip: !definition,
  });
  // The definition behind a configured workflow, read with the frame's variables.
  const behind = useWorkflowDefinition(configured?.definitionSlug ?? null, orgId);

  const raw = configured
    ? (configuredQ.runs.find((r) => r.guid === runId) ??
      configured.runs.find((r) => r.guid === runId) ??
      null)
    : null;
  // A guid of another definition's run under this slug is not this page's run.
  const defRun =
    definition && definitionRunQ.run?.definitionGuid === definition.guid
      ? definitionRunQ.run
      : null;
  const run: WorkflowRunSubject | null = raw
    ? configuredRunSubject(raw)
    : defRun
      ? definitionRunSubject(defRun)
      : null;
  const live = run?.live ?? false;
  const runsQ = configured ? configuredQ : definitionRunQ;

  const { startPolling, stopPolling } = runsQ;
  React.useEffect(() => {
    if (!live) return;
    startPolling(LIVE_POLL_MS);
    return () => stopPolling();
  }, [live, startPolling, stopPolling]);

  const historyQ = useWorkflowInstanceDetail(run?.temporalWorkflowId ?? null);
  const refetchHistory = historyQ.refetch;
  React.useEffect(() => {
    if (!live || !run?.temporalWorkflowId) return;
    const id = setInterval(() => void refetchHistory(), LIVE_POLL_MS);
    return () => clearInterval(id);
  }, [live, run?.temporalWorkflowId, refetchHistory]);

  const temporalRunId = run?.temporalRunId ?? historyQ.detail?.instance.runId ?? null;
  const executionsQ = useWorkflowStageExecutions({
    workflowId: run?.temporalWorkflowId ?? null,
    runId: temporalRunId,
    pollInterval: live ? LIVE_POLL_MS : 0,
  });

  const waitingGate = executionsQ.executions.some(
    (x) => x.stageKind === "human_gate" && x.humanGateState === "pending"
  );
  const gatesQ = usePendingHumanGates({
    orgId,
    pollInterval: waitingGate ? GATES_POLL_MS : 0,
    skip: !waitingGate,
  });
  const viewerGateIds = gatesQ.error
    ? null
    : gatesQ.gates
        .filter(
          (g) =>
            g.runGuid === runId ||
            (run?.temporalWorkflowId != null && g.workflowId === run.temporalWorkflowId)
        )
        .map((g) => g.executionId);

  const refetchAll = () => {
    void runsQ.refetch();
    void executionsQ.refetch();
    if (waitingGate) void gatesQ.refetch();
    if (run?.temporalWorkflowId) void refetchHistory();
  };

  const gate = useGateReview({
    workflowId: run?.temporalWorkflowId ?? null,
    onDecided: refetchAll,
  });

  const [cancelRun, { loading: cancelling }] = useCancelWorkflowInstance();
  async function onCancel(): Promise<boolean> {
    if (!run?.temporalWorkflowId) return false;
    const { data } = await cancelRun({ variables: { workflowId: run.temporalWorkflowId } });
    if (data?.cancelWorkflowInstance?.ok) {
      toast.success("Cancellation requested");
      refetchAll();
      return true;
    }
    toast.error(data?.cancelWorkflowInstance?.errors?.[0]?.messages?.[0] ?? "Cancel failed");
    return false;
  }

  const now = useNow(live);
  const plan = configured ? (behind.definition?.stages ?? []) : (definition?.stages ?? []);
  const history = historyQ.detail?.history ?? [];

  return {
    slug,
    workflowName: configured?.name ?? definition?.name ?? slug,
    runId,
    run,
    loading: runsQ.loading && !run,
    error: runsQ.error && !run ? { message: runsQ.error.message } : null,
    onRetry: () => void runsQ.refetch(),
    windowed: Boolean(definition),
    plan,
    executions: executionsQ.executions,
    executionsLoading: executionsQ.loading && executionsQ.executions.length === 0,
    executionsError: executionsQ.error ? executionsQ.error.message : null,
    onRetryExecutions: () => void executionsQ.refetch(),
    history,
    historyLoading: historyQ.loading && !historyQ.detail,
    historyError: historyQ.error ? historyQ.error.message : null,
    onRetryHistory: () => void refetchHistory(),
    onDownloadLog: () =>
      downloadText(
        `workflow-run-${runId}.log`,
        logText(runLogLines(plan, executionsQ.executions, history))
      ),
    now,
    motion,
    viewerGateIds,
    decide: gate.decide,
    decidingGuids: gate.decidingGuids,
    canCancel: entitlement.canRun && Boolean(run?.temporalWorkflowId),
    cancelling,
    onCancel,
  };
}
