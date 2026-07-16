export type MutationError = {
  field: string;
  messages: string[];
};

export type WorkflowMutationResult = {
  ok: boolean;
  errors: MutationError[];
};

export type StartWorkflowData = {
  startWorkflow: WorkflowMutationResult & {
    instanceId: string | null;
  };
};

export type TransitionWorkflowData = {
  transitionWorkflow: WorkflowMutationResult;
};

// ─── Temporal instance viewer (#437) ────────────────────────────────────

export type WorkflowInstance = {
  workflowId: string;
  workflowType: string;
  runId: string;
  status: string;
  startedAt: string;
  closedAt: string;
  durationSeconds: number | null;
  taskQueue: string;
  triggeredBy: string;
};

export type WorkflowInstancePage = {
  items: WorkflowInstance[];
  nextCursor: string | null;
};

export type WorkflowHistoryEvent = {
  eventType: string;
  timestamp: string;
  payload: Record<string, unknown>;
  retryCount: number;
  decision: string;
};

export type WorkflowInstanceDetail = {
  instance: WorkflowInstance;
  history: WorkflowHistoryEvent[];
};

export type WorkflowInstancesData = {
  astroliftWorkflowInstances: WorkflowInstancePage;
};

export type WorkflowInstanceDetailData = {
  astroliftWorkflowInstanceDetail: WorkflowInstanceDetail | null;
};

export type CancelWorkflowInstanceData = {
  cancelWorkflowInstance: WorkflowMutationResult;
};

export type TerminateWorkflowInstanceData = {
  terminateWorkflowInstance: WorkflowMutationResult;
};

export type SignalWorkflowInstanceData = {
  signalWorkflowInstance: WorkflowMutationResult;
};
