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

export interface AstroliftWebhookSubscription {
  id: AstroliftGuid;
  url: string;
  events: string[];
  isActive: boolean;
  lastDeliveryAt: string | null;
  lastResponseStatus: number | null;
  failureCount: number;
  createdAt: string;
}

export interface AstroliftNotification {
  id: AstroliftGuid;
  userId: string;
  kind: string;
  title: string;
  body: string;
  link: string;
  readAt: string | null;
  createdAt: string;
}

export interface AstroliftWebhookSecretReveal {
  subscription: AstroliftWebhookSubscription;
  plaintextSecret: string;
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
