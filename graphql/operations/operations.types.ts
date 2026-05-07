// Types matching astrolift_operations/schema/types.py

export type AstroliftGuid = string;

export interface AstroliftEvent {
  id: AstroliftGuid;
  eventType: string;
  payload: Record<string, unknown>;
  organizationId: string | null;
  teamId: string | null;
  projectId: string | null;
  registeredAppId: string | null;
  occurredAt: string;
}

export type AuditDecision = "ALLOW" | "DENY" | "UNKNOWN";

export interface AstroliftAuditEvent {
  id: AstroliftGuid;
  organizationId: string | null;
  occurredAt: string;
  actorKind: string;
  actorId: string;
  actorDisplay: string;
  action: string;
  decision: AuditDecision;
  targetKind: string;
  targetId: string;
  targetSlug: string;
  requestId: string;
  data: Record<string, unknown>;
}

export interface AstroliftWorkflowRun {
  id: AstroliftGuid;
  workflowKind: string;
  workflowId: string;
  runId: string;
  status:
    | "running"
    | "completed"
    | "failed"
    | "cancelled"
    | "terminated"
    | "timed_out";
  startedAt: string | null;
  endedAt: string | null;
  organizationId: string | null;
  registeredAppId: string | null;
  failure: Record<string, unknown> | null;
}
