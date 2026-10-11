"use client";

/**
 * Email-service detail sheet (#629, #631, #632, #633, #634).
 *
 * Renders the SES (or future GCP/Azure) observability surface for one
 * bound email managed-service: reputation, quota, cost, identity and DNS
 * authentication, suppressions, sender configuration, SNS publishing,
 * alert rules, engagement metrics, message logs and templates.
 *
 * The sheet is sectioned so one list shows at a time: Health and
 * Identity & sending hold the panels, and each list (suppressions, alert
 * rules, messages, templates) has a section of its own. Each list is an
 * embedded ListPage with its list state kept in the panel. Suppressions
 * are nested in the detail payload; alert rules, messages and templates
 * have separate queries wired by the route's containers.
 *
 * Backends that don't expose any of these (GCP, Azure) populate
 * `unsupportedNotes`; the UI shades the corresponding panel and shows
 * the upstream's hint instead of the data.
 *
 * The sheet renders inside the managed-services page so operators stay
 * in flow when debugging deliverability — no extra navigation.
 *
 * Pure views: the panels that talk to the server take their data from
 * the hooks in use-email-detail.ts, wired by the route's containers and
 * passed into the sheet as `panels`.
 */

import {
  AlertTriangleIcon,
  BellIcon,
  BarChart3Icon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  CoinsIcon,
  CopyIcon,
  FileTextIcon,
  GaugeIcon,
  HelpCircleIcon,
  InboxIcon,
  Loader2Icon,
  MailWarningIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  SendIcon,
  SettingsIcon,
  ShieldIcon,
  ShieldXIcon,
  TrashIcon,
  XCircleIcon,
} from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { type SelectRowsSpec, selectRows } from "@/components/list/select-rows";
import {
  type ListDefinition,
  type ListStateController,
  standardViews,
  useLocalListState,
} from "@/components/list/use-list-state";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type {
  AstroliftEmailDnsAuthCheck,
  AstroliftEmailMessage,
  AstroliftEmailServiceDetail,
  AstroliftEmailSuppressionEntry,
  AstroliftEmailTemplate,
  EmailDnsCheckOutcome,
} from "@/graphql/services/services.types";

import {
  defaultAlertName,
  type AlertRuleRow,
  type SesAlertKind,
  type useEmailCost,
  type useEmailTemplates,
  type useEngagementMetrics,
  type useMessageLog,
  type useSenderAlertRules,
  type useSenderConfig,
  type useSuppressionList,
  type useTemplateStats,
} from "./use-email-detail";

const OUTCOME_DOT: Record<EmailDnsCheckOutcome, string> = {
  GREEN: "bg-success",
  YELLOW: "bg-warning",
  RED: "bg-danger",
  UNKNOWN: "bg-muted-foreground",
};

const OUTCOME_LABEL: Record<EmailDnsCheckOutcome, string> = {
  GREEN: "Healthy",
  YELLOW: "Suboptimal",
  RED: "Broken",
  UNKNOWN: "Unknown",
};

function copyToClipboard(value: string, label = "Value") {
  void navigator.clipboard.writeText(value).then(
    () => toast.success(`${label} copied`),
    () => toast.error("Copy failed")
  );
}

function formatPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(digits)}%`;
}

function formatNumber(value: number, digits = 0): string {
  if (!Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatMoney(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(cents / 100);
}

/** The client-side step's input, from a panel's own list state. */
function pageOf(list: ListStateController) {
  return {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  };
}

const NOT_PERSONAL = "These belong to the email service, not a person, so Mine is empty.";

/** An embedded list's declaration over one payload's rows. */
function emailList(
  id: string,
  fields: ListDefinition["fields"],
  searchPlaceholder: string,
  defaultSort: ListDefinition["defaultSort"]
): ListDefinition {
  return {
    id: `managed-services.email.${id}`,
    fields,
    searchPlaceholder,
    defaultSort,
    views: standardViews({ owner: "me" }, [], { mineNote: NOT_PERSONAL }),
    paging: "numbered",
    pageSizes: [25, 50, 100],
  };
}

/** The panels that carry data of their own, rendered by the route's containers. */
export interface EmailDetailPanels {
  delivery?: React.ReactNode;
  cost?: React.ReactNode;
  suppression?: React.ReactNode;
  senderConfig?: React.ReactNode;
  alertRules?: React.ReactNode;
  engagement?: React.ReactNode;
  messageLog?: React.ReactNode;
  templates?: React.ReactNode;
}

/** The sheet's sections; one shows at a time. */
export type EmailDetailSection =
  | "delivery"
  | "health"
  | "sending"
  | "suppressions"
  | "alerts"
  | "messages"
  | "templates";

export interface EmailDetailSheetViewProps {
  serviceName: string;
  serviceConfig: Record<string, unknown>;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  detail: AstroliftEmailServiceDetail | null;
  loading: boolean;
  panels?: EmailDetailPanels;
  /** The section shown first (stories). */
  defaultSection?: EmailDetailSection;
  /**
   * Why Astrolift writes no DNS on this install (DNS withheld,
   * calliope-installer#447). A pending domain identity's records are the
   * operator's to publish. The note states that, not a cause: the driver only
   * ever publishes inside base_domain, which this view does not know.
   */
  dnsRestriction?: string | null;
}

export function EmailDetailSheetView({
  serviceName,
  serviceConfig,
  open,
  onOpenChange,
  detail,
  loading,
  panels,
  defaultSection = "health",
  dnsRestriction,
}: EmailDetailSheetViewProps) {
  const deliveryText = useTranslations("emailDelivery");
  const [section, setSection] = React.useState<EmailDetailSection>(defaultSection);
  const sections: { id: EmailDetailSection; label: string; shown: boolean }[] = [
    { id: "delivery", label: deliveryText("title"), shown: !!panels?.delivery },
    { id: "health", label: "Health", shown: true },
    { id: "sending", label: "Identity & sending", shown: true },
    { id: "suppressions", label: "Suppressions", shown: Boolean(panels?.suppression) },
    { id: "alerts", label: "Alert rules", shown: Boolean(panels?.alertRules) },
    { id: "messages", label: "Messages", shown: Boolean(panels?.messageLog) },
    { id: "templates", label: "Templates", shown: Boolean(panels?.templates) },
  ];
  const tabs = sections.filter((s) => s.shown);
  const active = tabs.some((s) => s.id === section) ? section : "health";
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Email service: {serviceName}</SheetTitle>
          <SheetDescription>
            Deliverability health, identity verification, and suppression list for this bound email
            backend.
          </SheetDescription>
        </SheetHeader>

        {panels?.delivery ? (
          <div className="p-1">
            <Button variant="outline" onClick={() => setSection("delivery")}>
              {deliveryText("title")}
            </Button>
            {active === "delivery" ? panels.delivery : null}
          </div>
        ) : null}
        {active === "delivery" ? null : loading && !detail ? (
          <div className="space-y-4 p-1">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        ) : detail ? (
          <div className="space-y-6 p-1">
            <div
              role="tablist"
              aria-label="Email service sections"
              className="bg-muted/40 flex max-w-full flex-wrap gap-1 rounded-md border p-1"
            >
              {tabs.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={active === t.id}
                  onClick={() => setSection(t.id)}
                  className={`rounded px-3 py-1 text-sm font-medium transition ${
                    active === t.id
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
            {active === "health" ? (
              <>
                <ReputationPanel detail={detail} />
                <QuotaPanel detail={detail} />
                {panels?.cost}
                {panels?.engagement}
                {detail.unsupportedNotes.length > 0 ? (
                  <Card className="bg-muted/40 p-3">
                    <p className="text-muted-foreground text-2xs mb-1 font-semibold uppercase">
                      Notes from {detail.pluginSlug.toUpperCase()}
                    </p>
                    <ul className="text-muted-foreground space-y-0.5 text-xs">
                      {detail.unsupportedNotes.map((note) => (
                        <li key={note} className="font-mono">
                          • {note}
                        </li>
                      ))}
                    </ul>
                  </Card>
                ) : null}
              </>
            ) : active === "sending" ? (
              <>
                <IdentityPanel detail={detail} dnsRestriction={dnsRestriction} />
                <DnsAuthPanel detail={detail} />
                {panels?.senderConfig}
                <SnsEventPublishingPanel serviceConfig={serviceConfig} />
              </>
            ) : active === "suppressions" ? (
              panels?.suppression
            ) : active === "alerts" ? (
              panels?.alertRules
            ) : active === "messages" ? (
              panels?.messageLog
            ) : (
              panels?.templates
            )}
          </div>
        ) : (
          <p className="text-muted-foreground p-3 text-sm">
            Email detail unavailable. This managed service may have been deprovisioned, or the cloud
            driver isn&apos;t reachable.
          </p>
        )}

        <SheetFooter className="mt-6">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

// ── Reputation / sandbox status (#633) ─────────────────────────────

function ReputationPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
  const status = detail.accountStatus;
  if (!status) {
    return (
      <PanelEmpty
        title="Sender reputation"
        message="The backend doesn't expose reputation / sandbox status."
        icon={<ShieldXIcon className="size-4" />}
      />
    );
  }

  const repPct = status.reputationScore !== null ? status.reputationScore * 100 : null;
  const repColor = (() => {
    if (repPct === null) return "bg-muted-foreground";
    if (repPct >= 70) return "bg-success";
    if (repPct >= 50) return "bg-warning";
    return "bg-danger";
  })();

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <ShieldIcon className="size-4" />
          Sender reputation
        </h3>
        <Badge
          variant={status.productionAccess ? "default" : "outline"}
          className={
            status.productionAccess
              ? "bg-success/10 text-success-fg"
              : "border-warning-border bg-warning/10 text-warning-fg"
          }
        >
          {status.productionAccess ? "Production access" : "Sandbox"}
        </Badge>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <p className="text-muted-foreground text-2xs uppercase">Reputation</p>
          <p className="font-mono text-lg">{repPct === null ? "—" : `${repPct.toFixed(0)}%`}</p>
          {repPct !== null ? (
            <div className="bg-muted mt-1.5 h-1.5 w-full overflow-hidden rounded-full">
              <div
                className={`h-full ${repColor}`}
                style={{ width: `${Math.min(100, Math.max(0, repPct))}%` }}
              />
            </div>
          ) : null}
        </div>
        <div>
          <p className="text-muted-foreground text-2xs uppercase">Bounce rate (7d)</p>
          <p className="font-mono text-lg">{formatPct(status.bounceRatePct)}</p>
          <p className="text-muted-foreground text-2xs">SES throttles at ~10%</p>
        </div>
        <div>
          <p className="text-muted-foreground text-2xs uppercase">Complaint rate (7d)</p>
          <p className="font-mono text-lg">{formatPct(status.complaintRatePct)}</p>
          <p className="text-muted-foreground text-2xs">SES throttles at ~0.5%</p>
        </div>
      </div>
      {!status.productionAccess ? (
        <p className="text-muted-foreground mt-3 flex items-start gap-1.5 text-xs">
          <AlertTriangleIcon className="text-warning-fg size-3.5 shrink-0" />
          Sandbox mode: this identity can only send to verified recipients + verified domains.
          Request production access from AWS Support before shipping to end users.
        </p>
      ) : null}
    </Card>
  );
}

// ── Quota tile (#629) ──────────────────────────────────────────────

function QuotaPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
  const quota = detail.quota;
  if (!quota) {
    return (
      <PanelEmpty
        title="Send quota"
        message="The backend doesn't expose send-rate quota."
        icon={<GaugeIcon className="size-4" />}
      />
    );
  }
  const dailyPct = quota.max24HourSend > 0 ? (quota.sentLast24h / quota.max24HourSend) * 100 : 0;
  const dailyOverThreshold = dailyPct > 80;
  return (
    <Card className="p-4">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <GaugeIcon className="size-4" />
        Send quota
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-muted-foreground text-2xs uppercase">Max send rate</p>
          <p className="font-mono text-lg">{formatNumber(quota.maxSendRate, 1)} /sec</p>
        </div>
        <div>
          <p className="text-muted-foreground text-2xs uppercase">Sent today</p>
          <p className="font-mono text-lg">
            {formatNumber(quota.sentLast24h)} /{" "}
            <span className="text-muted-foreground text-base">
              {formatNumber(quota.max24HourSend)}
            </span>
          </p>
          <div className="mt-2">
            <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
              <div
                className={`h-full ${dailyOverThreshold ? "bg-warning" : "bg-primary"}`}
                style={{ width: `${Math.min(100, dailyPct)}%` }}
              />
            </div>
            <p
              className={`text-2xs mt-1 ${
                dailyOverThreshold ? "text-warning-fg" : "text-muted-foreground"
              }`}
            >
              {dailyPct.toFixed(1)}% of 24h quota
              {dailyOverThreshold ? " — close to the daily ceiling" : ""}
            </p>
          </div>
        </div>
      </div>
    </Card>
  );
}

// ── Cost-per-period tile (#630) ────────────────────────────────────

export function CostPanelView({
  mtdSum,
  trailingSum,
  currency,
  loading,
}: ReturnType<typeof useEmailCost>) {
  const haveAny = (mtdSum !== null && mtdSum > 0) || (trailingSum !== null && trailingSum > 0);

  return (
    <Card className="p-4">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <CoinsIcon className="size-4" />
        Cost
      </h3>
      {loading && !haveAny ? (
        <div className="grid grid-cols-2 gap-4">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : !haveAny && mtdSum === null && trailingSum === null ? (
        <p className="text-muted-foreground text-xs">
          No cost data attributed to this service yet. Costs appear after the cost driver runs its
          next reconciliation pass.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-muted-foreground text-2xs uppercase">This month</p>
            <p className="font-mono text-lg tabular-nums">
              {mtdSum === null ? "—" : formatMoney(mtdSum, currency)}
            </p>
            <p className="text-muted-foreground text-2xs">Month-to-date</p>
          </div>
          <div>
            <p className="text-muted-foreground text-2xs uppercase">Last 30 days</p>
            <p className="font-mono text-lg tabular-nums">
              {trailingSum === null ? "—" : formatMoney(trailingSum, currency)}
            </p>
            <p className="text-muted-foreground text-2xs">Trailing 30-day spend</p>
          </div>
        </div>
      )}
    </Card>
  );
}

// ── Identity verification badge (#634) ─────────────────────────────

function IdentityPanel({
  detail,
  dnsRestriction,
}: {
  detail: AstroliftEmailServiceDetail;
  dnsRestriction?: string | null;
}) {
  const iv = detail.identityVerification;
  if (!iv) {
    return (
      <PanelEmpty
        title="Identity verification"
        message="The backend doesn't expose identity-verification details."
        icon={<HelpCircleIcon className="size-4" />}
      />
    );
  }

  const verified = iv.status === "Success";
  const failed = iv.status === "Failed";
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">Identity verification</h3>
        <Badge
          className={
            verified
              ? "bg-success/10 text-success-fg"
              : failed
                ? "bg-danger/10 text-danger-fg"
                : "bg-warning/10 text-warning-fg"
          }
          variant="outline"
        >
          {verified ? (
            <CheckCircle2Icon className="mr-1 size-3" />
          ) : failed ? (
            <XCircleIcon className="mr-1 size-3" />
          ) : (
            <Loader2Icon className="mr-1 size-3 animate-spin" />
          )}
          {iv.status}
        </Badge>
      </div>
      <p className="text-muted-foreground mb-2 text-xs">
        <span className="text-foreground font-mono">{iv.identity}</span> (
        {iv.isDomain ? "domain identity" : "email identity"})
      </p>
      {iv.isDomain && !verified && !failed && dnsRestriction ? (
        <p className="text-warning-fg mb-3 text-xs">
          Astrolift publishes no verification records on this install; publish them in the
          identity&apos;s DNS zone. {dnsRestriction}
        </p>
      ) : null}
      {iv.verificationToken ? (
        <div className="bg-muted/40 text-2xs mb-3 rounded-md border p-2 font-mono">
          <p className="text-muted-foreground">TXT verification challenge:</p>
          <div className="mt-0.5 flex items-center gap-2">
            <code className="flex-1 break-all">{iv.verificationToken}</code>
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              onClick={() => copyToClipboard(iv.verificationToken, "Token")}
            >
              <CopyIcon className="size-3" />
            </Button>
          </div>
        </div>
      ) : null}
      {iv.dkimTokens.length > 0 ? (
        <div className="space-y-1.5">
          <p className="text-muted-foreground text-2xs uppercase">DKIM CNAMEs to publish</p>
          {iv.dkimTokens.map((tok) => (
            <div
              key={tok.token}
              className="bg-muted/40 text-2xs flex items-center gap-2 rounded-md border p-2 font-mono"
            >
              <div className="min-w-0 flex-1">
                <p className="text-foreground truncate">{tok.cnameHost}</p>
                <p className="text-muted-foreground truncate">CNAME → {tok.cnameTarget}</p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="size-6 shrink-0"
                onClick={() =>
                  copyToClipboard(`${tok.cnameHost} CNAME ${tok.cnameTarget}`, "CNAME")
                }
              >
                <CopyIcon className="size-3" />
              </Button>
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
}

// ── DNS auth panel (#632) ──────────────────────────────────────────

function DnsAuthPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
  const dns = detail.dnsAuthStatus;
  if (!dns) {
    return (
      <PanelEmpty
        title="DKIM / SPF / DMARC"
        message="The backend doesn't expose DNS auth probing for this identity."
        icon={<ShieldIcon className="size-4" />}
      />
    );
  }
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">DKIM / SPF / DMARC</h3>
        <Badge variant="outline" className="flex items-center gap-1.5">
          <span className={`inline-block size-2 rounded-full ${OUTCOME_DOT[dns.overall]}`} />
          {OUTCOME_LABEL[dns.overall]}
        </Badge>
      </div>
      <div className="space-y-2">
        <DnsCheckRow check={dns.dkim} />
        <DnsCheckRow check={dns.spf} />
        <DnsCheckRow check={dns.dmarc} />
      </div>
      <p className="text-muted-foreground text-2xs mt-3">
        Probed {new Date(dns.checkedAt).toLocaleString()}
      </p>
    </Card>
  );
}

function DnsCheckRow({ check }: { check: AstroliftEmailDnsAuthCheck }) {
  return (
    <div className="flex items-start gap-3 rounded-md border p-2">
      <span
        className={`mt-1.5 inline-block size-2 shrink-0 rounded-full ${OUTCOME_DOT[check.outcome]}`}
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">
          {check.protocol}{" "}
          <span className="text-muted-foreground text-2xs">{OUTCOME_LABEL[check.outcome]}</span>
        </p>
        {check.message ? <p className="text-muted-foreground text-xs">{check.message}</p> : null}
        {check.records.length > 0 ? (
          <details className="mt-1">
            <summary className="text-muted-foreground text-2xs cursor-pointer uppercase">
              {check.records.length} record{check.records.length > 1 ? "s" : ""}
            </summary>
            <ul className="mt-1 space-y-0.5">
              {check.records.map((r) => (
                <li key={r} className="bg-muted/40 text-2xs rounded p-1.5 font-mono break-all">
                  {r}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </div>
  );
}

// ── Suppression list (#631) ────────────────────────────────────────

const suppressionKey = (e: AstroliftEmailSuppressionEntry) => e.address;

const SUPPRESSION_SELECT: SelectRowsSpec<AstroliftEmailSuppressionEntry> = {
  filter: { owner: () => false, reason: (e, value) => e.reason === value },
  text: (e) => [e.address, e.reason],
  sort: {
    address: (e) => e.address.toLowerCase(),
    reason: (e) => e.reason,
    date: (e) => e.suppressedAt,
  },
  id: suppressionKey,
};

function suppressionList(entries: AstroliftEmailSuppressionEntry[]): ListDefinition {
  const reasons = [...new Set(entries.map((e) => e.reason).filter(Boolean))].sort();
  return emailList(
    "suppressions",
    [{ key: "reason", label: "Reason", options: reasons.map((r) => ({ value: r, label: r })) }],
    "Search addresses…",
    [{ key: "date", dir: "desc" }]
  );
}

export type SuppressionPanelViewProps = ReturnType<typeof useSuppressionList> & {
  detail: AstroliftEmailServiceDetail;
};

export function SuppressionPanelView({
  detail,
  adding,
  removing,
  onAdd,
  onRemove,
}: SuppressionPanelViewProps) {
  const [addAddress, setAddAddress] = React.useState("");
  const [addNote, setAddNote] = React.useState("");
  const [removalTarget, setRemovalTarget] = React.useState<AstroliftEmailSuppressionEntry | null>(
    null
  );
  const [def] = React.useState(() => suppressionList(detail.suppressionEntries));
  const list = useLocalListState(def);
  const page = selectRows(detail.suppressionEntries, pageOf(list), SUPPRESSION_SELECT);
  const columns: Column<AstroliftEmailSuppressionEntry>[] = [
    {
      id: "address",
      header: "Address",
      sortKey: "address",
      cellClassName: "font-mono text-xs [overflow-wrap:anywhere]",
      cell: (entry) => entry.address,
    },
    {
      id: "reason",
      header: "Reason",
      sortKey: "reason",
      cell: (entry) => (
        <Badge variant="outline" className="text-2xs">
          {entry.reason}
        </Badge>
      ),
    },
    {
      id: "date",
      header: "Date",
      sortKey: "date",
      cellClassName: "text-muted-foreground text-xs",
      cell: (entry) => new Date(entry.suppressedAt).toLocaleDateString(),
    },
    {
      id: "remove",
      header: <span className="sr-only">Remove</span>,
      label: "Remove",
      width: "w-12",
      align: "right",
      cell: (entry) => (
        <Can permission="managed_service.update">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={() => setRemovalTarget(entry)}
                disabled={removing}
              >
                <TrashIcon className="size-3.5" />
                <span className="sr-only">Remove from suppression</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Un-suppress (allow sends again)</TooltipContent>
          </Tooltip>
        </Can>
      ),
    },
  ];

  async function handleAdd() {
    const address = addAddress.trim();
    if (!address) return;
    if (await onAdd(address, addNote.trim())) {
      setAddAddress("");
      setAddNote("");
    }
  }

  async function handleRemove(entry: AstroliftEmailSuppressionEntry) {
    if (await onRemove(entry)) setRemovalTarget(null);
  }

  const isUnsupported = detail.unsupportedNotes.some((n) => n.startsWith("suppression_entries"));

  if (isUnsupported) {
    return (
      <PanelEmpty
        title="Suppression list"
        message="The backend doesn't expose an account-level suppression list."
        icon={<TrashIcon className="size-4" />}
      />
    );
  }

  return (
    <Card className="p-4">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
        Suppression list
        <Badge variant="secondary" className="text-2xs">
          {detail.suppressionEntries.length}
        </Badge>
      </h3>

      <Can permission="managed_service.update">
        <div className="bg-muted/30 mb-3 rounded-md border p-3">
          <p className="text-muted-foreground text-2xs mb-2 uppercase">Add manual entry</p>
          <div className="space-y-2">
            <div>
              <Label htmlFor="suppress-address" className="text-xs">
                Address
              </Label>
              <Input
                id="suppress-address"
                value={addAddress}
                onChange={(e) => setAddAddress(e.target.value)}
                placeholder="recipient@example.com"
                className="font-mono text-xs"
                disabled={adding}
              />
            </div>
            <div>
              <Label htmlFor="suppress-note" className="text-xs">
                Note (audit trail)
              </Label>
              <Input
                id="suppress-note"
                value={addNote}
                onChange={(e) => setAddNote(e.target.value)}
                placeholder="e.g. support ticket #4567"
                className="text-xs"
                disabled={adding}
              />
            </div>
            <Button
              size="sm"
              onClick={handleAdd}
              disabled={adding || !addAddress.trim()}
              className="w-full"
            >
              {adding ? <Loader2Icon className="mr-1.5 size-3 animate-spin" /> : null}
              Suppress address
            </Button>
          </div>
        </div>
      </Can>

      {detail.suppressionEntries.length === 0 ? (
        <p className="text-muted-foreground py-4 text-center text-xs">No suppressed addresses.</p>
      ) : (
        <ListPage<AstroliftEmailSuppressionEntry>
          embedded
          list={list}
          label="Suppressed addresses"
          columns={columns}
          rows={page.rows}
          getRowId={suppressionKey}
          totalCount={page.totalCount}
          empty={{ icon: <TrashIcon className="size-5" />, title: "No suppressed addresses." }}
        />
      )}

      <AlertDialog
        open={removalTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRemovalTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Un-suppress {removalTarget?.address}?</AlertDialogTitle>
            <AlertDialogDescription>
              Removes this address from the SES account-level suppression list. SES will accept
              future sends to this recipient. If the original suppression was a legitimate hard
              bounce, re-suppressing after another failure may damage sender reputation.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={removing}
              onClick={() => {
                if (removalTarget) {
                  void handleRemove(removalTarget);
                }
              }}
            >
              {removing ? <Loader2Icon className="mr-1.5 size-3 animate-spin" /> : null}
              Un-suppress
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

// ── Sender config (from-name, reply-to, env senders) (#637, #638) ───

const SENDER_FIELDS: ReadonlyArray<{
  key: string;
  label: string;
  placeholder: string;
  hint: string;
}> = [
  {
    key: "from_name",
    label: "From / display name",
    placeholder: "Pulse Notifications",
    hint: "Shown in recipients' inboxes alongside the From address.",
  },
  {
    key: "reply_to",
    label: "Reply-to address",
    placeholder: "support@example.com",
    hint: "Where replies land if it differs from the From address.",
  },
  {
    key: "return_path",
    label: "Return-path (bounce handling)",
    placeholder: "bounce@example.com",
    hint: "Envelope sender; bounce notifications return here.",
  },
  {
    key: "production_from",
    label: "Production sender",
    placeholder: "noreply@example.com",
    hint: "Used when an app is deployed to the production environment.",
  },
  {
    key: "preview_from",
    label: "Preview sender",
    placeholder: "preview@example.com",
    hint: "Used in preview envs so test mail can't impersonate production.",
  },
];

export function SenderConfigPanelView({
  serviceConfig,
  saving: loading,
  onSave,
}: ReturnType<typeof useSenderConfig>) {
  const initial = React.useMemo(
    () =>
      Object.fromEntries(
        SENDER_FIELDS.map((f) => [
          f.key,
          typeof serviceConfig[f.key] === "string" ? (serviceConfig[f.key] as string) : "",
        ])
      ),
    [serviceConfig]
  );
  const [values, setValues] = React.useState<Record<string, string>>(initial);
  const [editing, setEditing] = React.useState(false);

  React.useEffect(() => {
    if (!editing) setValues(initial);
  }, [editing, initial]);

  const dirty = SENDER_FIELDS.some((f) => (values[f.key] ?? "") !== initial[f.key]);

  async function handleSave() {
    if (
      await onSave(
        values,
        SENDER_FIELDS.map((f) => f.key)
      )
    ) {
      setEditing(false);
    }
  }

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <SettingsIcon className="size-4" />
          Sender config
        </h3>
        <Can permission="managed_service.update">
          {editing ? (
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="ghost"
                disabled={loading}
                onClick={() => setEditing(false)}
              >
                Cancel
              </Button>
              <Button size="sm" disabled={loading || !dirty} onClick={() => void handleSave()}>
                {loading ? <Loader2Icon className="mr-1.5 size-3 animate-spin" /> : null}
                Save
              </Button>
            </div>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              <PencilIcon className="mr-1 size-3" />
              Edit
            </Button>
          )}
        </Can>
      </div>
      <div className="space-y-3">
        {SENDER_FIELDS.map((f) => {
          const v = values[f.key] ?? "";
          const displayValue = initial[f.key];
          return (
            <div key={f.key} className="space-y-1">
              <Label
                htmlFor={`sender-${f.key}`}
                className="text-muted-foreground text-xs uppercase"
              >
                {f.label}
              </Label>
              {editing ? (
                <Input
                  id={`sender-${f.key}`}
                  value={v}
                  onChange={(e) =>
                    setValues((prev) => ({
                      ...prev,
                      [f.key]: e.target.value,
                    }))
                  }
                  placeholder={f.placeholder}
                  disabled={loading}
                  className="font-mono text-xs"
                />
              ) : (
                <p className="font-mono text-xs">
                  {displayValue ? (
                    displayValue
                  ) : (
                    <span className="text-muted-foreground italic">not set</span>
                  )}
                </p>
              )}
              {editing ? <p className="text-muted-foreground text-2xs">{f.hint}</p> : null}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ── SNS event publishing (#636) ────────────────────────────────────

function SnsEventPublishingPanel({ serviceConfig }: { serviceConfig: Record<string, unknown> }) {
  const arn =
    typeof serviceConfig.sns_event_destination_arn === "string"
      ? (serviceConfig.sns_event_destination_arn as string)
      : "";
  const configSet =
    typeof serviceConfig.configuration_set === "string"
      ? (serviceConfig.configuration_set as string)
      : "";
  const enabled = arn.length > 0;
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <SendIcon className="size-4" />
          SES event publishing
        </h3>
        <Badge
          variant="outline"
          className={
            enabled
              ? "bg-success/10 text-success-fg"
              : "border-warning-border bg-warning/10 text-warning-fg"
          }
        >
          {enabled ? "Active" : "Not configured"}
        </Badge>
      </div>
      {enabled ? (
        <>
          <p className="text-muted-foreground mb-2 text-xs">
            SES is publishing send / delivery / bounce / complaint / open / click events to the
            platform&apos;s ingester via SNS. The recent-message log and engagement metrics below
            stay live.
          </p>
          <div className="bg-muted/40 text-2xs mb-2 flex items-center gap-2 rounded-md border p-2 font-mono">
            <code className="flex-1 truncate" title={arn}>
              {arn}
            </code>
            <Button
              variant="ghost"
              size="icon"
              className="size-6 shrink-0"
              onClick={() => copyToClipboard(arn, "Topic ARN")}
            >
              <CopyIcon className="size-3" />
            </Button>
          </div>
          {configSet ? (
            <p className="text-muted-foreground text-2xs">
              Configuration set: <code className="text-foreground font-mono">{configSet}</code>
            </p>
          ) : null}
        </>
      ) : (
        <div className="text-muted-foreground space-y-2 text-xs">
          <p>
            SNS event publishing is not configured for this identity. Without it the platform
            can&apos;t render the per-message log, bounce drill-in, or open/click engagement.
          </p>
          <p>
            Set{" "}
            <code className="text-foreground font-mono">ASTROLIFT_SES_EVENTS_SNS_TOPIC_ARN</code> in
            the install&apos;s environment config and re-provision this managed service to wire the
            SES configuration set, SNS topic, and platform subscription.
          </p>
        </div>
      )}
    </Card>
  );
}

// ── Sender alert rules (#627, #639) ────────────────────────────────

const SES_ALERT_KINDS: ReadonlyArray<{
  value: "ses_bounce_rate" | "ses_complaint_rate";
  label: string;
  defaultThreshold: number;
  unit: string;
  hint: string;
}> = [
  {
    value: "ses_bounce_rate",
    label: "Bounce rate >",
    defaultThreshold: 5,
    unit: "%",
    hint: "SES throttles at ~10%. Warn early so you have time to react.",
  },
  {
    value: "ses_complaint_rate",
    label: "Complaint rate >",
    defaultThreshold: 0.1,
    unit: "%",
    hint: "SES throttles at ~0.5%. The ceiling is unforgiving here.",
  },
];

const SEVERITY_OPTIONS = [
  { value: "critical", label: "Critical" },
  { value: "warning", label: "Warning" },
  { value: "info", label: "Info" },
] as const;

function severityBadge(severity: string): string {
  switch (severity) {
    case "critical":
      return "border-danger-border bg-danger/10 text-danger-fg";
    case "warning":
      return "border-warning-border bg-warning/10 text-warning-fg";
    default:
      return "border-info-border bg-info/10 text-info-fg";
  }
}

const ALERT_RULES_LIST = emailList(
  "alert-rules",
  [
    { key: "severity", label: "Severity", options: SEVERITY_OPTIONS.map((o) => ({ ...o })) },
    {
      key: "status",
      label: "Status",
      options: [
        { value: "active", label: "Active" },
        { value: "inactive", label: "Inactive" },
      ],
    },
  ],
  "Search rules, conditions…",
  [{ key: "name", dir: "asc" }]
);

const SEVERITY_RANK: Record<string, number> = { critical: 0, warning: 1, info: 2 };

const ALERT_RULES_SELECT: SelectRowsSpec<AlertRuleRow> = {
  filter: {
    owner: () => false,
    severity: (r, value) => r.severity === value,
    status: (r, value) => r.isActive === (value === "active"),
  },
  text: (r) => [r.name, predicateLabel(r.predicate), r.severity],
  sort: {
    name: (r) => r.name.toLowerCase(),
    severity: (r) => SEVERITY_RANK[r.severity] ?? 9,
    status: (r) => (r.isActive ? 0 : 1),
  },
  id: (r) => r.id,
};

export function AlertRulesPanelView({
  rules,
  loading,
  creating,
  deleting,
  onCreate,
  onDelete,
}: ReturnType<typeof useSenderAlertRules>) {
  const [kind, setKind] = React.useState<SesAlertKind>("ses_bounce_rate");
  const [threshold, setThreshold] = React.useState<string>("5");
  const [severity, setSeverity] = React.useState<string>("warning");
  const [name, setName] = React.useState("");
  const list = useLocalListState(ALERT_RULES_LIST);
  const page = selectRows(rules, pageOf(list), ALERT_RULES_SELECT);
  const columns: Column<AlertRuleRow>[] = [
    {
      id: "name",
      header: "Name",
      sortKey: "name",
      cellClassName: "text-xs [overflow-wrap:anywhere]",
      cell: (r) => r.name,
    },
    {
      id: "condition",
      header: "Condition",
      cellClassName: "text-muted-foreground text-2xs font-mono [overflow-wrap:anywhere]",
      cell: (r) => predicateLabel(r.predicate),
    },
    {
      id: "severity",
      header: "Severity",
      sortKey: "severity",
      cell: (r) => (
        <Badge variant="outline" className={`capitalize ${severityBadge(r.severity)}`}>
          {r.severity}
        </Badge>
      ),
    },
    {
      id: "status",
      header: "Status",
      sortKey: "status",
      cellClassName: "text-xs",
      cell: (r) => (r.isActive ? "Active" : "Inactive"),
    },
    {
      id: "delete",
      header: <span className="sr-only">Delete</span>,
      label: "Delete",
      width: "w-10",
      align: "right",
      cell: (r) => (
        <Can permission="managed_service.update">
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            disabled={deleting}
            onClick={() => void onDelete(r)}
          >
            <TrashIcon className="size-3.5" />
            <span className="sr-only">Delete alert rule</span>
          </Button>
        </Can>
      ),
    },
  ];

  React.useEffect(() => {
    const preset = SES_ALERT_KINDS.find((k) => k.value === kind);
    if (preset) setThreshold(String(preset.defaultThreshold));
  }, [kind]);

  async function handleCreate() {
    if (await onCreate({ kind, threshold, severity, name })) setName("");
  }

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <BellIcon className="size-4" />
          Alert rules
          <Badge variant="secondary" className="text-2xs">
            {rules.length}
          </Badge>
        </h3>
      </div>
      {loading && rules.length === 0 ? (
        <Skeleton className="h-12 w-full" />
      ) : rules.length === 0 ? (
        <p className="text-muted-foreground mb-3 text-xs">
          No alert rules wired up. Add one below — defaults match SES enforcement headroom so you
          hear before the throttle.
        </p>
      ) : (
        <ListPage<AlertRuleRow>
          embedded
          list={list}
          label="Alert rules"
          columns={columns}
          rows={page.rows}
          getRowId={(r) => r.id}
          totalCount={page.totalCount}
          empty={{ icon: <BellIcon className="size-5" />, title: "No alert rules" }}
        />
      )}

      <Can permission="managed_service.update">
        <div className="bg-muted/30 mt-3 rounded-md border p-3">
          <p className="text-muted-foreground text-2xs mb-2 uppercase">Add alert</p>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs">Condition</Label>
              <Select value={kind} onValueChange={(v) => setKind(v as SesAlertKind)}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SES_ALERT_KINDS.map((k) => (
                    <SelectItem key={k.value} value={k.value}>
                      {k.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Threshold (%)</Label>
              <Input
                type="number"
                min={0}
                step="any"
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
                className="h-8 font-mono text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Severity</Label>
              <Select value={severity} onValueChange={setSeverity}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEVERITY_OPTIONS.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Name (optional)</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={defaultAlertName(kind, Number(threshold) || 0)}
                className="h-8 text-xs"
              />
            </div>
          </div>
          <Button
            size="sm"
            className="mt-3 w-full"
            disabled={creating}
            onClick={() => void handleCreate()}
          >
            {creating ? (
              <Loader2Icon className="mr-1.5 size-3 animate-spin" />
            ) : (
              <PlusIcon className="mr-1.5 size-3" />
            )}
            Create alert rule
          </Button>
          <p className="text-muted-foreground text-2xs mt-2">
            {SES_ALERT_KINDS.find((k) => k.value === kind)?.hint}
          </p>
        </div>
      </Can>
    </Card>
  );
}

function predicateLabel(predicate: Record<string, unknown>): string {
  const kind = typeof predicate.kind === "string" ? (predicate.kind as string) : null;
  const threshold =
    typeof predicate.threshold_pct === "number"
      ? (predicate.threshold_pct as number)
      : typeof predicate.threshold === "number"
        ? (predicate.threshold as number)
        : null;
  if (kind === "ses_bounce_rate" && threshold !== null) {
    return `bounce_rate > ${threshold}%`;
  }
  if (kind === "ses_complaint_rate" && threshold !== null) {
    return `complaint_rate > ${threshold}%`;
  }
  return JSON.stringify(predicate);
}

// ── Engagement metrics (#626) ──────────────────────────────────────

export type EngagementMetricsPanelViewProps = ReturnType<typeof useEngagementMetrics> & {
  snsConfigured: boolean;
};

export function EngagementMetricsPanelView({
  metrics: m,
  loading,
  snsConfigured,
}: EngagementMetricsPanelViewProps) {
  const tiles: Array<{
    label: string;
    pct: number;
    count: number;
    denomLabel: string;
    denom: number;
    warnAt?: number;
    critAt?: number;
    direction: "higher_better" | "lower_better";
  }> = m
    ? [
        {
          label: "Open rate",
          pct: m.openRatePct,
          count: m.totalOpens,
          denomLabel: "deliveries",
          denom: m.totalDeliveries,
          direction: "higher_better",
        },
        {
          label: "Click rate",
          pct: m.clickRatePct,
          count: m.totalClicks,
          denomLabel: "deliveries",
          denom: m.totalDeliveries,
          direction: "higher_better",
        },
        {
          label: "Bounce rate",
          pct: m.bounceRatePct,
          count: m.totalBounces,
          denomLabel: "sends",
          denom: m.totalSends,
          warnAt: 5,
          critAt: 10,
          direction: "lower_better",
        },
        {
          label: "Complaint rate",
          pct: m.complaintRatePct,
          count: m.totalComplaints,
          denomLabel: "sends",
          denom: m.totalSends,
          warnAt: 0.1,
          critAt: 0.5,
          direction: "lower_better",
        },
      ]
    : [];

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <BarChart3Icon className="size-4" />
          Engagement metrics
        </h3>
        {m ? <span className="text-muted-foreground text-2xs">Last {m.windowDays}d</span> : null}
      </div>
      {!snsConfigured ? (
        <p className="text-muted-foreground mb-3 text-xs">
          Engagement counts come from SES event publishing. Configure the SNS topic above to start
          collecting opens, clicks, bounces, and complaints; the tiles will populate from the next
          batch.
        </p>
      ) : null}
      {loading && !m ? (
        <div className="grid grid-cols-2 gap-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : !m || m.totalSends === 0 ? (
        <p className="text-muted-foreground text-xs">No engagement data in the last 30 days.</p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {tiles.map((t) => {
            const tone =
              t.direction === "lower_better"
                ? t.critAt && t.pct >= t.critAt
                  ? "text-danger-fg"
                  : t.warnAt && t.pct >= t.warnAt
                    ? "text-warning-fg"
                    : "text-success-fg"
                : "text-foreground";
            return (
              <div key={t.label} className="bg-muted/30 rounded-md border p-3">
                <p className="text-muted-foreground text-2xs uppercase">{t.label}</p>
                <p className={`font-mono text-2xl tabular-nums ${tone}`}>{formatPct(t.pct, 2)}</p>
                <p className="text-muted-foreground text-2xs">
                  {formatNumber(t.count)} of {formatNumber(t.denom)} {t.denomLabel}
                </p>
              </div>
            );
          })}
        </div>
      )}
      {m && m.totalSends > 0 ? (
        <p className="text-muted-foreground text-2xs mt-3">
          {formatNumber(m.totalSends)} sends · {formatNumber(m.totalDeliveries)} delivered · last{" "}
          {m.windowDays}d
        </p>
      ) : null}
    </Card>
  );
}

// ── Message log + bounce/complaint drill-in (#624, #625) ───────────

const EVENT_KIND_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "All events" },
  { value: "delivery", label: "Delivered" },
  { value: "bounce", label: "Bounced" },
  { value: "complaint", label: "Complaints" },
  { value: "open", label: "Opens" },
  { value: "click", label: "Clicks" },
  { value: "send", label: "Sends" },
  { value: "reject", label: "Rejected" },
];

function eventKindBadge(kind: string): string {
  switch (kind) {
    case "delivery":
      return "border-success-border bg-success/10 text-success-fg";
    case "bounce":
      return "border-danger-border bg-danger/10 text-danger-fg";
    case "complaint":
      return "border-danger-border bg-danger/10 text-danger-fg";
    case "open":
    case "click":
      return "border-info-border bg-info/10 text-info-fg";
    case "reject":
      return "border-warning-border bg-warning/10 text-warning-fg";
    default:
      return "border-muted-foreground/30 bg-muted/40";
  }
}

/**
 * Event and recipient are the query's filters (the hook sends them); search,
 * sort and numbered pages run over the page of messages it returns.
 */
const MESSAGES_LIST = emailList(
  "messages",
  [
    {
      key: "event",
      label: "Event",
      options: EVENT_KIND_OPTIONS.filter((o) => o.value !== "").map((o) => ({ ...o })),
    },
    { key: "recipient", label: "Recipient" },
  ],
  "Search recipients, subjects…",
  [{ key: "when", dir: "desc" }]
);

const MESSAGES_SELECT: SelectRowsSpec<AstroliftEmailMessage> = {
  filter: { owner: () => false },
  text: (m) => [m.recipient, m.subject, m.eventKind],
  sort: {
    recipient: (m) => m.recipient.toLowerCase(),
    subject: (m) => m.subject.toLowerCase(),
    event: (m) => m.eventKind,
    when: (m) => m.occurredAt,
  },
  id: (m) => m.id,
};

export function MessageLogPanelView({
  messages,
  loading,
  onRefresh,
  eventKind,
  onEventKindChange,
  onApplyRecipient,
}: ReturnType<typeof useMessageLog>) {
  const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set<string>());
  const detailsId = React.useId();
  const list = useLocalListState(MESSAGES_LIST, {
    filters: eventKind ? { event: eventKind } : {},
  });
  const event = list.filters.event ?? "";
  const recipient = (list.filters.recipient ?? "").trim();
  // The event and recipient chips are the query's filters: hand them to the hook.
  React.useEffect(() => {
    onEventKindChange(event);
  }, [event, onEventKindChange]);
  React.useEffect(() => {
    onApplyRecipient(recipient);
  }, [recipient, onApplyRecipient]);
  const page = selectRows(messages, pageOf(list), MESSAGES_SELECT);

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const columns: Column<AstroliftEmailMessage>[] = [
    {
      id: "recipient",
      header: "Recipient",
      sortKey: "recipient",
      cellClassName: "min-w-0",
      cell: (m) => {
        const isOpen = expanded.has(m.id);
        const hasMetadata = m.metadata && Object.keys(m.metadata).length > 0;
        if (!hasMetadata) {
          return (
            <span className="text-2xs pl-5 font-mono [overflow-wrap:anywhere]">{m.recipient}</span>
          );
        }
        const Chevron = isOpen ? ChevronDownIcon : ChevronRightIcon;
        return (
          <div className="flex min-w-0 flex-col gap-1">
            <button
              type="button"
              onClick={() => toggleExpanded(m.id)}
              aria-expanded={isOpen}
              aria-controls={`${detailsId}-${m.id}`}
              className="flex min-w-0 items-center gap-1.5 text-left"
            >
              <Chevron className="size-3.5 shrink-0" />
              <span className="text-2xs font-mono [overflow-wrap:anywhere]">{m.recipient}</span>
            </button>
            {isOpen ? (
              <div
                id={`${detailsId}-${m.id}`}
                role="region"
                aria-label={`Message details for ${m.recipient}`}
                className="bg-muted/30 rounded-md"
              >
                <MessageMetadata
                  eventKind={m.eventKind}
                  messageId={m.messageId}
                  metadata={m.metadata}
                />
              </div>
            ) : null}
          </div>
        );
      },
    },
    {
      id: "subject",
      header: "Subject",
      sortKey: "subject",
      cellClassName: "max-w-[14rem] truncate align-top text-xs",
      cell: (m) =>
        m.subject ? (
          <span title={m.subject}>{m.subject}</span>
        ) : (
          <span className="text-muted-foreground italic">(no subject)</span>
        ),
    },
    {
      id: "event",
      header: "Event",
      sortKey: "event",
      cellClassName: "align-top",
      cell: (m) => (
        <Badge variant="outline" className={`text-2xs ${eventKindBadge(m.eventKind)}`}>
          {m.eventKind}
        </Badge>
      ),
    },
    {
      id: "when",
      header: "When",
      sortKey: "when",
      cellClassName: "text-muted-foreground text-2xs align-top",
      cell: (m) => new Date(m.occurredAt).toLocaleString(),
    },
  ];

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <InboxIcon className="size-4" />
          Recent messages
          <Badge variant="secondary" className="text-2xs">
            {messages.length}
          </Badge>
        </h3>
        <Button variant="ghost" size="sm" onClick={onRefresh} disabled={loading}>
          <RefreshCwIcon className={`mr-1 size-3 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>
      <ListPage<AstroliftEmailMessage>
        embedded
        list={list}
        label="Messages"
        columns={columns}
        rows={page.rows}
        getRowId={(m) => m.id}
        totalCount={page.totalCount}
        loading={loading && messages.length === 0}
        stale={loading && messages.length > 0}
        empty={{
          icon: <InboxIcon className="size-5" />,
          title: "No messages",
          description:
            "Either nothing has been sent in the retention window or SNS event publishing isn't configured.",
        }}
      />
    </Card>
  );
}

function MessageMetadata({
  eventKind,
  messageId,
  metadata,
}: {
  eventKind: string;
  messageId: string;
  metadata: Record<string, unknown>;
}) {
  const bounceType =
    typeof metadata.bounce_type === "string" ? (metadata.bounce_type as string) : null;
  const bounceSubType =
    typeof metadata.bounce_sub_type === "string" ? (metadata.bounce_sub_type as string) : null;
  const complaintType =
    typeof metadata.complaint_feedback_type === "string"
      ? (metadata.complaint_feedback_type as string)
      : null;
  const diagnostic =
    typeof metadata.diagnostic_code === "string" ? (metadata.diagnostic_code as string) : null;

  return (
    <div className="space-y-2 p-3 text-xs">
      <div className="text-2xs flex items-center gap-2 font-mono">
        <span className="text-muted-foreground">message-id</span>
        <code className="text-foreground truncate" title={messageId}>
          {messageId}
        </code>
        <Button
          variant="ghost"
          size="icon"
          className="size-5"
          aria-label="Copy message ID"
          onClick={(e) => {
            e.stopPropagation();
            copyToClipboard(messageId, "Message ID");
          }}
        >
          <CopyIcon className="size-3" />
        </Button>
      </div>
      {eventKind === "bounce" && (bounceType || bounceSubType) ? (
        <div className="flex items-center gap-2">
          <MailWarningIcon className="text-danger-fg size-3.5" />
          <span>
            <strong>{bounceType ?? "bounce"}</strong>
            {bounceSubType ? ` · ${bounceSubType}` : ""}
          </span>
        </div>
      ) : null}
      {eventKind === "complaint" && complaintType ? (
        <div className="flex items-center gap-2">
          <MailWarningIcon className="text-danger-fg size-3.5" />
          <span>
            feedback-loop: <strong>{complaintType}</strong>
          </span>
        </div>
      ) : null}
      {diagnostic ? (
        <div className="text-2xs font-mono">
          <span className="text-muted-foreground">diagnostic:</span> <span>{diagnostic}</span>
        </div>
      ) : null}
      <details>
        <summary className="text-muted-foreground text-2xs cursor-pointer uppercase">
          Raw metadata
        </summary>
        <pre className="bg-background/60 text-2xs mt-1 max-h-48 overflow-auto rounded-md border p-2 font-mono break-all whitespace-pre-wrap">
          {JSON.stringify(metadata, null, 2)}
        </pre>
      </details>
    </div>
  );
}

// ── Template management (#635, #628) ───────────────────────────────

const TEMPLATES_LIST = emailList("templates", [], "Search templates, subjects…", [
  { key: "name", dir: "asc" },
]);

const TEMPLATES_SELECT: SelectRowsSpec<AstroliftEmailTemplate> = {
  filter: { owner: () => false },
  text: (t) => [t.name, t.subject],
  sort: {
    name: (t) => t.name.toLowerCase(),
    subject: (t) => t.subject.toLowerCase(),
    created: (t) => t.createdAt ?? "",
  },
  id: (t) => t.name,
};

export type TemplatesPanelViewProps = ReturnType<typeof useEmailTemplates> & {
  /** One template's 14d send stats, mounted only while its row is expanded. */
  renderStats: (name: string) => React.ReactNode;
};

export function TemplatesPanelView({
  templates,
  loading,
  saving: busy,
  deleting,
  onSave,
  onDelete,
  renderStats,
}: TemplatesPanelViewProps) {
  const [creatingNew, setCreatingNew] = React.useState(false);
  const [editingName, setEditingName] = React.useState<string | null>(null);
  const [statsOpen, setStatsOpen] = React.useState<Set<string>>(() => new Set<string>());
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftEmailTemplate | null>(null);
  const list = useLocalListState(TEMPLATES_LIST);
  const page = selectRows(templates, pageOf(list), TEMPLATES_SELECT);

  const [draft, setDraft] = React.useState({
    name: "",
    subject: "",
    htmlBody: "",
    textBody: "",
  });

  function resetDraft() {
    setDraft({ name: "", subject: "", htmlBody: "", textBody: "" });
  }

  function beginCreate() {
    setCreatingNew(true);
    setEditingName(null);
    resetDraft();
  }

  function beginEdit(t: AstroliftEmailTemplate) {
    setEditingName(t.name);
    setCreatingNew(false);
    setDraft({
      name: t.name,
      subject: t.subject,
      htmlBody: t.htmlBody,
      textBody: t.textBody,
    });
  }

  function cancelForm() {
    setCreatingNew(false);
    setEditingName(null);
    resetDraft();
  }

  function toggleStats(name: string) {
    setStatsOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  async function handleSave() {
    if (await onSave(draft, creatingNew ? null : editingName)) cancelForm();
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    if (await onDelete(deleteTarget)) setDeleteTarget(null);
  }

  const formOpen = creatingNew || editingName !== null;

  const columns: Column<AstroliftEmailTemplate>[] = [
    {
      id: "name",
      header: "Name",
      sortKey: "name",
      cellClassName: "min-w-0",
      cell: (t) => (
        <div className="flex min-w-0 flex-col gap-2">
          <span className="font-mono text-xs [overflow-wrap:anywhere]">{t.name}</span>
          {statsOpen.has(t.name) ? (
            <div className="bg-muted/30 rounded-md p-3">{renderStats(t.name)}</div>
          ) : null}
        </div>
      ),
    },
    {
      id: "subject",
      header: "Subject",
      sortKey: "subject",
      cellClassName: "max-w-[14rem] truncate align-top text-xs",
      cell: (t) => <span title={t.subject}>{t.subject}</span>,
    },
    {
      id: "created",
      header: "Created",
      sortKey: "created",
      cellClassName: "text-muted-foreground text-2xs align-top",
      cell: (t) => (t.createdAt ? new Date(t.createdAt).toLocaleDateString() : "unknown"),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      label: "Actions",
      width: "w-28",
      align: "right",
      cellClassName: "align-top",
      cell: (t) => (
        <div className="inline-flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={() => toggleStats(t.name)}
            title="Toggle 14d send stats"
            aria-expanded={statsOpen.has(t.name)}
          >
            <BarChart3Icon className="size-3.5" />
            <span className="sr-only">Stats</span>
          </Button>
          <Can permission="managed_service.update">
            <Button variant="ghost" size="icon" className="size-7" onClick={() => beginEdit(t)}>
              <PencilIcon className="size-3.5" />
              <span className="sr-only">Edit</span>
            </Button>
          </Can>
          <Can permission="managed_service.update">
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => setDeleteTarget(t)}
              disabled={deleting}
            >
              <TrashIcon className="size-3.5" />
              <span className="sr-only">Delete</span>
            </Button>
          </Can>
        </div>
      ),
    },
  ];

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <FileTextIcon className="size-4" />
          Templates
          <Badge variant="secondary" className="text-2xs">
            {templates.length}
          </Badge>
        </h3>
        <Can permission="managed_service.update">
          {!formOpen ? (
            <Button size="sm" variant="ghost" onClick={beginCreate}>
              <PlusIcon className="mr-1 size-3" />
              New template
            </Button>
          ) : null}
        </Can>
      </div>

      {formOpen ? (
        <div className="bg-muted/30 mb-3 space-y-2 rounded-md border p-3">
          <p className="text-muted-foreground text-2xs uppercase">
            {creatingNew ? "New template" : `Editing ${editingName}`}
          </p>
          <div className="space-y-1">
            <Label className="text-xs">Name</Label>
            <Input
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              placeholder="welcome_email"
              disabled={!creatingNew || busy}
              className="font-mono text-xs"
            />
            {!creatingNew ? (
              <p className="text-muted-foreground text-2xs">
                Name is the template identifier and can&apos;t be changed.
              </p>
            ) : null}
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Subject</Label>
            <Input
              value={draft.subject}
              onChange={(e) => setDraft((d) => ({ ...d, subject: e.target.value }))}
              placeholder="Welcome to {{app_name}}"
              disabled={busy}
              className="text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">HTML body</Label>
            <Textarea
              value={draft.htmlBody}
              onChange={(e) => setDraft((d) => ({ ...d, htmlBody: e.target.value }))}
              rows={6}
              placeholder="<h1>Hello {{name}}</h1>"
              disabled={busy}
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Text body</Label>
            <Textarea
              value={draft.textBody}
              onChange={(e) => setDraft((d) => ({ ...d, textBody: e.target.value }))}
              rows={4}
              placeholder="Hello {{name}}"
              disabled={busy}
              className="font-mono text-xs"
            />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button size="sm" variant="ghost" onClick={cancelForm} disabled={busy}>
              Cancel
            </Button>
            <Button size="sm" disabled={busy} onClick={() => void handleSave()}>
              {busy ? <Loader2Icon className="mr-1.5 size-3 animate-spin" /> : null}
              {creatingNew ? "Create" : "Save changes"}
            </Button>
          </div>
        </div>
      ) : null}

      {loading && templates.length === 0 ? (
        <div className="space-y-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : templates.length === 0 ? (
        <p className="text-muted-foreground py-3 text-center text-xs">
          No templates yet. Create one above to start sending templated email.
        </p>
      ) : (
        <ListPage<AstroliftEmailTemplate>
          embedded
          list={list}
          label="Templates"
          columns={columns}
          rows={page.rows}
          getRowId={(t) => t.name}
          totalCount={page.totalCount}
          empty={{ icon: <FileTextIcon className="size-5" />, title: "No templates" }}
        />
      )}

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete template {deleteTarget?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Removes the SES template from the cloud account. Workloads that send via this template
              will fail until you recreate it or update the app to use a different template.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction disabled={deleting} onClick={() => void handleDelete()}>
              {deleting ? <Loader2Icon className="mr-1.5 size-3 animate-spin" /> : null}
              Delete template
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

export function TemplateStatsView({ name, points, loading }: ReturnType<typeof useTemplateStats>) {
  if (loading && points.length === 0) {
    return <Skeleton className="h-16 w-full" />;
  }
  if (points.length === 0) {
    return (
      <p className="text-muted-foreground text-xs">
        No send activity for {name} in the last 14 days.
      </p>
    );
  }

  const max = Math.max(...points.map((p) => p.sends), 1);
  const totals = points.reduce(
    (acc, p) => ({
      sends: acc.sends + p.sends,
      deliveries: acc.deliveries + p.deliveries,
      bounces: acc.bounces + p.bounces,
      complaints: acc.complaints + p.complaints,
    }),
    { sends: 0, deliveries: 0, bounces: 0, complaints: 0 }
  );

  return (
    <div className="space-y-2">
      <div className="flex items-end gap-0.5">
        {points.map((p) => {
          const pct = (p.sends / max) * 100;
          const failed = p.bounces + p.complaints;
          const failedPct = p.sends > 0 ? (failed / p.sends) * 100 : 0;
          return (
            <Tooltip key={p.timestamp}>
              <TooltipTrigger asChild>
                <div className="bg-muted/60 relative flex-1 rounded-sm" style={{ height: "44px" }}>
                  <div
                    className="bg-primary/60 absolute right-0 bottom-0 left-0 rounded-sm"
                    style={{ height: `${pct}%` }}
                  />
                  {failed > 0 ? (
                    <div
                      className="bg-danger/70 absolute right-0 bottom-0 left-0 rounded-sm"
                      style={{ height: `${(failedPct * pct) / 100}%` }}
                    />
                  ) : null}
                </div>
              </TooltipTrigger>
              <TooltipContent className="text-2xs">
                <div className="font-mono">
                  <div>{new Date(p.timestamp).toLocaleDateString()}</div>
                  <div>
                    {p.sends} sends · {p.deliveries} delivered
                  </div>
                  {p.bounces > 0 ? <div>{p.bounces} bounces</div> : null}
                  {p.complaints > 0 ? <div>{p.complaints} complaints</div> : null}
                </div>
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
      <p className="text-muted-foreground text-2xs">
        14d · {formatNumber(totals.sends)} sends · {formatNumber(totals.deliveries)} delivered ·{" "}
        {formatNumber(totals.bounces)} bounces · {formatNumber(totals.complaints)} complaints
      </p>
    </div>
  );
}

// ── Empty-state cell ─────────────────────────────────────────────

function PanelEmpty({
  title,
  message,
  icon,
}: {
  title: string;
  message: string;
  icon: React.ReactNode;
}) {
  return (
    <Card className="bg-muted/30 p-4">
      <div className="text-muted-foreground flex items-start gap-2">
        <span className="mt-0.5">{icon}</span>
        <div>
          <p className="text-foreground text-sm font-semibold">{title}</p>
          <p className="text-xs">{message}</p>
        </div>
      </div>
    </Card>
  );
}
