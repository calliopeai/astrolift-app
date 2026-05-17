/**
 * Operations types — facade over the codegen output.
 */

import type {
  AstroliftAuditEvent as GeneratedAuditEvent,
  AstroliftAuditEventPage as GeneratedAuditEventPage,
  AstroliftAuditExport as GeneratedAuditExport,
  AstroliftAuditRetention as GeneratedAuditRetention,
  AstroliftEvent as GeneratedEvent,
  AstroliftNotification as GeneratedNotification,
  AstroliftWebhookDelivery as GeneratedWebhookDelivery,
  AstroliftWebhookSubscription as GeneratedWebhookSubscription,
  AstroliftWebhookTestResult as GeneratedWebhookTestResult,
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

export type AstroliftAuditEventPage = Omit<GeneratedAuditEventPage, "items"> & {
  items: AstroliftAuditEvent[];
};

export type AuditExportFormat = "csv" | "ndjson";

export type AstroliftAuditExport = Omit<GeneratedAuditExport, "format"> & {
  format: AuditExportFormat;
};

export type AstroliftAuditRetention = GeneratedAuditRetention;

export type WebhookFormat = "generic" | "slack" | "discord";

export type AstroliftWebhookSubscription = Omit<GeneratedWebhookSubscription, "format"> & {
  format: WebhookFormat;
};

export type AstroliftWebhookDelivery = GeneratedWebhookDelivery;

export type AstroliftWebhookTestResult = GeneratedWebhookTestResult;

export type AstroliftNotification = GeneratedNotification;

export type AstroliftWebhookSecretReveal = GeneratedWebhookSecretReveal;

export type AstroliftWorkflowRun = Omit<GeneratedWorkflowRun, "status"> & {
  status: WorkflowRunStatus;
};
