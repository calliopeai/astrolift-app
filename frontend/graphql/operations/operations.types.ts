/**
 * Operations types — facade over the codegen output.
 */

import type {
  AstroliftAuditEvent as GeneratedAuditEvent,
  AstroliftEvent as GeneratedEvent,
  AstroliftNotification as GeneratedNotification,
  AstroliftWebhookSubscription as GeneratedWebhookSubscription,
  AstroliftWorkflowRun as GeneratedWorkflowRun,
  WebhookSecretReveal as GeneratedWebhookSecretReveal,
} from "@/graphql/__generated__/schema";

export type AstroliftGuid = string;

export type AuditDecision = "ALLOW" | "DENY" | "UNKNOWN";

export type WorkflowRunStatus =
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "terminated"
  | "timed_out";

export type AstroliftEvent = GeneratedEvent;

export type AstroliftAuditEvent = Omit<GeneratedAuditEvent, "decision"> & {
  decision: AuditDecision;
};

export type AstroliftWebhookSubscription = GeneratedWebhookSubscription;

export type AstroliftNotification = GeneratedNotification;

export type AstroliftWebhookSecretReveal = GeneratedWebhookSecretReveal;

export type AstroliftWorkflowRun = Omit<GeneratedWorkflowRun, "status"> & {
  status: WorkflowRunStatus;
};
