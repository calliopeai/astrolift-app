import type { ComponentProps } from "react";

import { fakeController } from "@/components/data-table/fixtures";
import type { ManagedServiceMetricsPanelProps } from "@/components/observability/ManagedServiceMetricsPanel";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import type {
  AstroliftEmailMessage,
  AstroliftEmailServiceDetail,
  AstroliftEmailTemplate,
  AstroliftTemplateSendStatPoint,
} from "@/graphql/services/services.types";

import type {
  CostPanelView,
  EmailDetailSheetViewProps,
  EngagementMetricsPanelViewProps,
  MessageLogPanelView,
  SenderConfigPanelView,
  SuppressionPanelViewProps,
  TemplatesPanelViewProps,
  TemplateStatsView,
  AlertRulesPanelView,
} from "./EmailDetailSheet";
import type { ManagedServicesScreenProps } from "./ManagedServicesScreen";
import type { ManagedServiceRow } from "./ServiceDetailSheet";
import type { AlertRuleRow } from "./use-email-detail";
import type { ManagedService } from "./use-managed-services";

/**
 * Hand-typed fixtures for the app managed-services group: the service
 * table screen, the generic service sheet, and the email detail sheet
 * with each of its data-backed panels.
 */

const noop = () => {};
const resolvedTrue = async () => true;
const resolvedVoid = async () => {};

// ── Service table ──────────────────────────────────────────────────

export function service(name: string, patch: Partial<ManagedService> = {}): ManagedService {
  return {
    id: `ms-${name}`,
    name,
    kind: "postgres",
    variant: "db.t4g.medium",
    status: "active",
    statusError: "",
    config: {},
    environmentName: "production",
    registeredAppSlug: "storefront",
    createdAt: "2026-09-01T12:00:00Z",
    updatedAt: "2026-09-27T09:30:00Z",
    lastActionAt: "2026-09-27T09:30:00Z",
    lastActionKind: "provision",
    ...patch,
  };
}

export const SERVICES: ManagedService[] = [
  service("checkout-db", {
    kind: "rds_postgres",
    config: { backup_enabled: true, snapshot_policy: "daily-7d" },
  }),
  service("sessions", { kind: "redis", variant: "cache.t4g.small", status: "provisioning" }),
  service("assets", {
    kind: "s3_bucket",
    variant: "",
    config: { public_access_blocked: false },
  }),
  service("transactional", {
    kind: "email",
    variant: "",
    config: { sns_event_destination_arn: "arn:aws:sns:us-west-2:123456789012:ses-events" },
  }),
  service("notifications", {
    kind: "ses_email",
    variant: "",
    status: "failed",
    statusError: "SES identity verification timed out after 72h; re-publish the TXT record.",
    config: { dkim_verified: false, domain_status: "pending_verification" },
  }),
  service("orders-queue", {
    kind: "sqs",
    variant: "",
    status: "deleted",
    environmentName: "preview",
  }),
];

function env(name: string): AstroliftAppEnvironment {
  return {
    id: `env-${name}`,
    name,
    registeredAppSlug: "storefront",
    createdAt: "2026-09-01T12:00:00Z",
    deploysPaused: false,
    ingressPaused: false,
    requiredApprovals: 0,
    settings: [],
    url: `https://${name}.storefront.example.com`,
  };
}

export const ENVS: AstroliftAppEnvironment[] = [env("production"), env("staging"), env("preview")];

export const SCREEN: ManagedServicesScreenProps = {
  slug: "storefront",
  table: fakeController<ManagedService>({ rows: SERVICES, totalCount: SERVICES.length }),
  envs: ENVS,
  busy: false,
  deprovisioning: false,
  onProvision: resolvedTrue,
  onDeprovision: resolvedTrue,
};

const LONG_NAME =
  "checkout-primary-database-with-a-deliberately-long-service-name-for-overflow-testing";

export const LONG_SERVICES: ManagedService[] = [
  service(LONG_NAME, {
    kind: "aurora_serverless",
    variant: "serverless-v2-min-0.5-max-128-acu-with-a-long-variant-label",
    environmentName: "production-us-west-2-blue-green-candidate",
    config: {
      backup_enabled: false,
      snapshot_policy: "hourly-24h-then-daily-35d-then-weekly-52w-cross-region",
    },
    statusError:
      "InvalidParameterCombination: the requested minimum capacity is below the engine version's floor; upgrade the engine or raise min_acu before retrying the provision workflow.",
  }),
];

// ── Generic service sheet ──────────────────────────────────────────

export const SERVICE_ROW: ManagedServiceRow = {
  id: "ms-checkout-db",
  name: "checkout-db",
  kind: "rds_postgres",
  variant: "db.t4g.medium",
  environmentName: "production",
  status: "active",
};

export const METRICS: ManagedServiceMetricsPanelProps = {
  range: "1h",
  onRangeChange: noop,
  data: {
    kind: "postgres",
    managedServiceId: "ms-checkout-db",
    name: "checkout-db",
    rangeSeconds: 3600,
    series: [
      {
        name: "connections",
        unit: "count",
        source: "cloudwatch",
        samples: Array.from({ length: 12 }, (_, i) => ({
          ts: new Date(Date.parse("2026-09-28T12:00:00Z") + i * 300_000).toISOString(),
          value: 20 + (i % 4) * 3,
        })),
      },
    ],
  },
  loading: false,
};

// ── Email detail ───────────────────────────────────────────────────

export const EMAIL_DETAIL: AstroliftEmailServiceDetail = {
  managedServiceId: "ms-transactional",
  pluginSlug: "ses",
  region: "us-west-2",
  identity: "mail.storefront.example.com",
  quota: { maxSendRate: 14, max24HourSend: 50000, sentLast24h: 42150 },
  accountStatus: {
    sendingEnabled: true,
    productionAccess: true,
    reputationScore: 0.92,
    bounceRatePct: 1.24,
    complaintRatePct: 0.03,
  },
  identityVerification: {
    identity: "mail.storefront.example.com",
    isDomain: true,
    status: "Success",
    verificationToken: "pmBGN/7MjnfhTKUZ06Enqq1PeGUaOkw8lGhcfwefcHU=",
    dkimTokens: [
      {
        token: "abc123",
        cnameHost: "abc123._domainkey.mail.storefront.example.com",
        cnameTarget: "abc123.dkim.amazonses.com",
      },
      {
        token: "def456",
        cnameHost: "def456._domainkey.mail.storefront.example.com",
        cnameTarget: "def456.dkim.amazonses.com",
      },
    ],
  },
  dnsAuthStatus: {
    identity: "mail.storefront.example.com",
    checkedAt: "2026-09-28T09:15:00Z",
    overall: "YELLOW",
    dkim: {
      protocol: "DKIM",
      outcome: "GREEN",
      records: ["abc123.dkim.amazonses.com", "def456.dkim.amazonses.com"],
      message: "",
    },
    spf: {
      protocol: "SPF",
      outcome: "GREEN",
      records: ["v=spf1 include:amazonses.com ~all"],
      message: "",
    },
    dmarc: {
      protocol: "DMARC",
      outcome: "YELLOW",
      records: ["v=DMARC1; p=none; rua=mailto:dmarc@storefront.example.com"],
      message: "Policy is p=none; consider quarantine once reports look clean.",
    },
  },
  suppressionEntries: [
    {
      address: "bounced@customer.example.com",
      reason: "BOUNCE",
      suppressedAt: "2026-09-20T10:00:00Z",
      detail: "",
    },
    {
      address: "angry@customer.example.com",
      reason: "COMPLAINT",
      suppressedAt: "2026-09-22T16:30:00Z",
      detail: "",
    },
  ],
  unsupportedNotes: [],
};

/** A backend that exposes none of the SES panels (GCP, Azure). */
export const EMAIL_DETAIL_UNSUPPORTED: AstroliftEmailServiceDetail = {
  managedServiceId: "ms-transactional",
  pluginSlug: "gcp",
  region: "us-central1",
  identity: "mail.storefront.example.com",
  quota: null,
  accountStatus: null,
  identityVerification: null,
  dnsAuthStatus: null,
  suppressionEntries: [],
  unsupportedNotes: [
    "suppression_entries: the provider has no account-level suppression list",
    "quota: send quota is not exposed by this backend",
  ],
};

export const EMAIL_CONFIG: Record<string, unknown> = {
  sns_event_destination_arn: "arn:aws:sns:us-west-2:123456789012:ses-events",
  configuration_set: "storefront-events",
  from_name: "Storefront",
  reply_to: "support@storefront.example.com",
};

export const EMAIL_SHEET: EmailDetailSheetViewProps = {
  serviceName: "transactional",
  serviceConfig: EMAIL_CONFIG,
  open: true,
  onOpenChange: noop,
  detail: EMAIL_DETAIL,
  loading: false,
};

export const COST: ComponentProps<typeof CostPanelView> = {
  mtdSum: 1834,
  trailingSum: 2410,
  currency: "USD",
  loading: false,
};

export const SUPPRESSION: SuppressionPanelViewProps = {
  detail: EMAIL_DETAIL,
  adding: false,
  removing: false,
  onAdd: resolvedTrue,
  onRemove: resolvedTrue,
};

export const SENDER_CONFIG: ComponentProps<typeof SenderConfigPanelView> = {
  serviceConfig: EMAIL_CONFIG,
  saving: false,
  onSave: resolvedTrue,
};

export const ALERT_RULES_LIST: AlertRuleRow[] = [
  {
    id: "ar-1",
    name: "Bounce rate > 5%",
    target: "managed_service",
    targetId: "ms-transactional",
    managedServiceId: "ms-transactional",
    severity: "warning",
    predicate: { kind: "ses_bounce_rate", threshold_pct: 5 },
    notifyChannels: [{ kind: "in_app", ref: "" }],
    isActive: true,
    createdAt: "2026-09-10T08:00:00Z",
  },
  {
    id: "ar-2",
    name: "Complaint rate > 0.1%",
    target: "managed_service",
    targetId: "ms-transactional",
    managedServiceId: "ms-transactional",
    severity: "critical",
    predicate: { kind: "ses_complaint_rate", threshold_pct: 0.1 },
    notifyChannels: [{ kind: "in_app", ref: "" }],
    isActive: false,
    createdAt: "2026-09-11T08:00:00Z",
  },
];

export const ALERT_RULES: ComponentProps<typeof AlertRulesPanelView> = {
  rules: ALERT_RULES_LIST,
  loading: false,
  creating: false,
  deleting: false,
  onCreate: resolvedTrue,
  onDelete: resolvedVoid,
};

export const ENGAGEMENT: EngagementMetricsPanelViewProps = {
  metrics: {
    totalSends: 42150,
    totalDeliveries: 41630,
    totalBounces: 520,
    totalComplaints: 21,
    totalOpens: 17890,
    totalClicks: 3120,
    bounceRatePct: 1.23,
    complaintRatePct: 0.05,
    openRatePct: 42.97,
    clickRatePct: 7.49,
    windowDays: 30,
  },
  loading: false,
  snsConfigured: true,
};

export const MESSAGES_LIST: AstroliftEmailMessage[] = [
  {
    id: "m-1",
    messageId: "0101019234abcd-11112222-3333-4444-5555-666677778888-000000",
    recipient: "ada@customer.example.com",
    subject: "Your order has shipped",
    eventKind: "delivery",
    occurredAt: "2026-09-28T09:40:00Z",
    metadata: {},
  },
  {
    id: "m-2",
    messageId: "0101019234abce-11112222-3333-4444-5555-666677778888-000000",
    recipient: "bounced@customer.example.com",
    subject: "Reset your password",
    eventKind: "bounce",
    occurredAt: "2026-09-28T09:35:00Z",
    metadata: {
      bounce_type: "Permanent",
      bounce_sub_type: "NoEmail",
      diagnostic_code: "smtp; 550 5.1.1 user unknown",
    },
  },
  {
    id: "m-3",
    messageId: "0101019234abcf-11112222-3333-4444-5555-666677778888-000000",
    recipient: "angry@customer.example.com",
    subject: "",
    eventKind: "complaint",
    occurredAt: "2026-09-28T09:20:00Z",
    metadata: { complaint_feedback_type: "abuse" },
  },
];

export const MESSAGE_LOG: ComponentProps<typeof MessageLogPanelView> = {
  messages: MESSAGES_LIST,
  loading: false,
  onRefresh: noop,
  eventKind: "",
  onEventKindChange: noop,
  onApplyRecipient: noop,
};

export const TEMPLATES_LIST: AstroliftEmailTemplate[] = [
  {
    name: "welcome_email",
    subject: "Welcome to {{app_name}}",
    htmlBody: "<h1>Hello {{name}}</h1>",
    textBody: "Hello {{name}}",
    createdAt: "2026-09-02T10:00:00Z",
  },
  {
    name: "order_shipped",
    subject: "Your order {{order_id}} has shipped",
    htmlBody: "<p>On its way.</p>",
    textBody: "On its way.",
    createdAt: null,
  },
];

export const TEMPLATES: Omit<TemplatesPanelViewProps, "renderStats"> = {
  templates: TEMPLATES_LIST,
  loading: false,
  saving: false,
  deleting: false,
  onSave: resolvedTrue,
  onDelete: resolvedTrue,
};

export const STAT_POINTS: AstroliftTemplateSendStatPoint[] = Array.from({ length: 14 }, (_, i) => ({
  timestamp: new Date(Date.parse("2026-09-15T00:00:00Z") + i * 86_400_000).toISOString(),
  sends: 120 + ((i * 37) % 90),
  deliveries: 115 + ((i * 37) % 90),
  bounces: i % 5 === 0 ? 4 : 0,
  complaints: i % 7 === 0 ? 1 : 0,
}));

export const TEMPLATE_STATS: ComponentProps<typeof TemplateStatsView> = {
  name: "welcome_email",
  points: STAT_POINTS,
  loading: false,
};

// ── Long strings ───────────────────────────────────────────────────

const LONG_ADDRESS =
  "a.recipient.with.a.deliberately.long.local.part.for.overflow@subdomain.customer-example-company.com";

export const EMAIL_DETAIL_LONG: AstroliftEmailServiceDetail = {
  ...EMAIL_DETAIL,
  identity: "transactional-mail.eu-central-1.storefront-with-a-long-domain.example.com",
  identityVerification: {
    ...EMAIL_DETAIL.identityVerification!,
    identity: "transactional-mail.eu-central-1.storefront-with-a-long-domain.example.com",
    status: "Pending",
  },
  suppressionEntries: [
    { address: LONG_ADDRESS, reason: "MANUAL", suppressedAt: "2026-09-25T10:00:00Z", detail: "" },
  ],
  unsupportedNotes: [
    "engagement_metrics: open and click tracking require a configuration set with an SNS destination whose subscription is confirmed by the platform ingester",
  ],
};

export const MESSAGES_LONG: AstroliftEmailMessage[] = [
  {
    id: "m-long",
    messageId: "0101019234abcd-11112222-3333-4444-5555-666677778888-000000-with-extra-suffix",
    recipient: LONG_ADDRESS,
    subject:
      "Your order from the storefront has shipped and here is a deliberately long subject line",
    eventKind: "bounce",
    occurredAt: "2026-09-28T09:35:00Z",
    metadata: {
      bounce_type: "Transient",
      bounce_sub_type: "MailboxFull",
      diagnostic_code:
        "smtp; 452 4.2.2 The email account that you tried to reach is over quota and inactive; please direct the recipient to their provider's support page",
    },
  },
];
