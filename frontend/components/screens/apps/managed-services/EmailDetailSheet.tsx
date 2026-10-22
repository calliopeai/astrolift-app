"use client";

/**
 * Email-service detail sheet (#629, #631, #632, #633, #634).
 *
 * Renders the SES (or future GCP/Azure) observability surface for one
 * bound email managed-service: reputation, quota, identity and DNS
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
import { useFormatter, useTranslations } from "next-intl";
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
import { useFormatters } from "@/lib/i18n/formatters";
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
  GREEN: "outcome.healthy",
  YELLOW: "outcome.suboptimal",
  RED: "outcome.broken",
  UNKNOWN: "outcome.unknown",
};

type Translate = (key: string, values?: Record<string, string | number>) => string;
type Formatter = ReturnType<typeof useFormatter>;

function copyToClipboard(value: string, copied: string, failed: string) {
  void navigator.clipboard.writeText(value).then(
    () => toast.success(copied),
    () => toast.error(failed)
  );
}

function formatPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(digits)}%`;
}

function formatNumber(fmt: Formatter, value: number, digits = 0): string {
  if (!Number.isFinite(value)) return "—";
  return fmt.number(value, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
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

/** An embedded list's declaration over one payload's rows. */
function emailList(
  t: Translate,
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
    views: standardViews({ owner: "me" }, [], {
      mineNote: t("notPersonal"),
      labels: { all: t("allView"), mine: t("mineView") },
    }),
    paging: "numbered",
    pageSizes: [25, 50, 100],
  };
}

/** The panels that carry data of their own, rendered by the route's containers. */
export interface EmailDetailPanels {
  delivery?: React.ReactNode;
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
  const t = useTranslations("appEmailService");
  const [section, setSection] = React.useState<EmailDetailSection>(defaultSection);
  const sections: { id: EmailDetailSection; label: string; shown: boolean }[] = [
    { id: "delivery", label: deliveryText("title"), shown: !!panels?.delivery },
    { id: "health", label: t("sections.health"), shown: true },
    { id: "sending", label: t("sections.sending"), shown: true },
    {
      id: "suppressions",
      label: t("sections.suppressions"),
      shown: Boolean(panels?.suppression),
    },
    { id: "alerts", label: t("sections.alerts"), shown: Boolean(panels?.alertRules) },
    { id: "messages", label: t("sections.messages"), shown: Boolean(panels?.messageLog) },
    { id: "templates", label: t("sections.templates"), shown: Boolean(panels?.templates) },
  ];
  const tabs = sections.filter((s) => s.shown);
  const active = tabs.some((s) => s.id === section) ? section : "health";
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>{t("title", { name: serviceName })}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
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
              aria-label={t("sectionsLabel")}
              className="bg-muted/40 flex max-w-full flex-wrap gap-1 rounded-md border p-1"
            >
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={active === tab.id}
                  onClick={() => setSection(tab.id)}
                  className={`rounded px-3 py-1 text-sm font-medium transition ${
                    active === tab.id
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            {active === "health" ? (
              <>
                <ReputationPanel detail={detail} />
                <QuotaPanel detail={detail} />
                {panels?.engagement}
                {detail.unsupportedNotes.length > 0 ? (
                  <Card className="bg-muted/40 p-3">
                    <p className="text-muted-foreground text-2xs mb-1 font-semibold uppercase">
                      {t("notesFrom", { plugin: detail.pluginSlug.toUpperCase() })}
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
          <p className="text-muted-foreground p-3 text-sm">{t("unavailable")}</p>
        )}

        <SheetFooter className="mt-6">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("close")}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

// ── Sending account, shared with the domain Email delivery page (#2289) ──

/** Sandbox or production access and send quota for the exact selected service. */
export function SendingAccountView({
  detail,
  loading,
}: {
  detail: AstroliftEmailServiceDetail | null;
  loading: boolean;
}) {
  const t = useTranslations("appEmailService");
  if (!detail) {
    return loading ? (
      <div className="grid gap-3 md:grid-cols-2">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    ) : (
      <p className="text-muted-foreground text-sm">{t("unavailable")}</p>
    );
  }
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <ReputationPanel detail={detail} />
      <QuotaPanel detail={detail} />
    </div>
  );
}

// ── Reputation / sandbox status (#633) ─────────────────────────────

function ReputationPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
  const t = useTranslations("appEmailService.reputation");
  const status = detail.accountStatus;
  if (!status) {
    return (
      <PanelEmpty
        title={t("title")}
        message={t("unsupported")}
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
          {t("title")}
        </h3>
        <Badge
          variant={status.productionAccess ? "default" : "outline"}
          className={
            status.productionAccess
              ? "bg-success/10 text-success-fg"
              : "border-warning-border bg-warning/10 text-warning-fg"
          }
        >
          {status.productionAccess ? t("productionAccess") : t("sandbox")}
        </Badge>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <p className="text-muted-foreground text-2xs uppercase">{t("reputation")}</p>
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
          <p className="text-muted-foreground text-2xs uppercase">{t("bounceRate")}</p>
          <p className="font-mono text-lg">{formatPct(status.bounceRatePct)}</p>
          <p className="text-muted-foreground text-2xs">{t("bounceThrottle")}</p>
        </div>
        <div>
          <p className="text-muted-foreground text-2xs uppercase">{t("complaintRate")}</p>
          <p className="font-mono text-lg">{formatPct(status.complaintRatePct)}</p>
          <p className="text-muted-foreground text-2xs">{t("complaintThrottle")}</p>
        </div>
      </div>
      {!status.productionAccess ? (
        <p className="text-muted-foreground mt-3 flex items-start gap-1.5 text-xs">
          <AlertTriangleIcon className="text-warning-fg size-3.5 shrink-0" />
          {t("sandboxNote")}
        </p>
      ) : null}
    </Card>
  );
}

// ── Quota tile (#629) ──────────────────────────────────────────────

function QuotaPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
  const t = useTranslations("appEmailService.quota");
  const fmt = useFormatter();
  const quota = detail.quota;
  if (!quota) {
    return (
      <PanelEmpty
        title={t("title")}
        message={t("unsupported")}
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
        {t("title")}
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-muted-foreground text-2xs uppercase">{t("maxSendRate")}</p>
          <p className="font-mono text-lg">
            {t("perSecond", { rate: formatNumber(fmt, quota.maxSendRate, 1) })}
          </p>
        </div>
        <div>
          <p className="text-muted-foreground text-2xs uppercase">{t("sentToday")}</p>
          <p className="font-mono text-lg">
            {formatNumber(fmt, quota.sentLast24h)} /{" "}
            <span className="text-muted-foreground text-base">
              {formatNumber(fmt, quota.max24HourSend)}
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
              {t(dailyOverThreshold ? "nearCeiling" : "ofQuota", { pct: dailyPct.toFixed(1) })}
            </p>
          </div>
        </div>
      </div>
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
  const t = useTranslations("appEmailService");
  const iv = detail.identityVerification;
  if (!iv) {
    return (
      <PanelEmpty
        title={t("identity.title")}
        message={t("identity.unsupported")}
        icon={<HelpCircleIcon className="size-4" />}
      />
    );
  }

  const verified = iv.status === "Success";
  const failed = iv.status === "Failed";
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">{t("identity.title")}</h3>
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
        {iv.isDomain ? t("identity.domainIdentity") : t("identity.emailIdentity")})
      </p>
      {iv.isDomain && !verified && !failed && dnsRestriction ? (
        <p className="text-warning-fg mb-3 text-xs">
          {t("identity.dnsWithheld")} {dnsRestriction}
        </p>
      ) : null}
      {iv.verificationToken ? (
        <div className="bg-muted/40 text-2xs mb-3 rounded-md border p-2 font-mono">
          <p className="text-muted-foreground">{t("identity.txtChallenge")}</p>
          <div className="mt-0.5 flex items-center gap-2">
            <code className="flex-1 break-all">{iv.verificationToken}</code>
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              onClick={() =>
                copyToClipboard(iv.verificationToken, t("copied.token"), t("copyFailed"))
              }
            >
              <CopyIcon className="size-3" />
            </Button>
          </div>
        </div>
      ) : null}
      {iv.dkimTokens.length > 0 ? (
        <div className="space-y-1.5">
          <p className="text-muted-foreground text-2xs uppercase">{t("identity.dkimCnames")}</p>
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
                  copyToClipboard(
                    `${tok.cnameHost} CNAME ${tok.cnameTarget}`,
                    t("copied.cname"),
                    t("copyFailed")
                  )
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
  const t = useTranslations("appEmailService");
  const { formatDateTime } = useFormatters();
  const dns = detail.dnsAuthStatus;
  if (!dns) {
    return (
      <PanelEmpty
        title="DKIM / SPF / DMARC"
        message={t("dns.unsupported")}
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
          {t(OUTCOME_LABEL[dns.overall])}
        </Badge>
      </div>
      <div className="space-y-2">
        <DnsCheckRow check={dns.dkim} />
        <DnsCheckRow check={dns.spf} />
        <DnsCheckRow check={dns.dmarc} />
      </div>
      <p className="text-muted-foreground text-2xs mt-3">
        {t("dns.probed", { time: formatDateTime(dns.checkedAt) })}
      </p>
    </Card>
  );
}

function DnsCheckRow({ check }: { check: AstroliftEmailDnsAuthCheck }) {
  const t = useTranslations("appEmailService");
  return (
    <div className="flex items-start gap-3 rounded-md border p-2">
      <span
        className={`mt-1.5 inline-block size-2 shrink-0 rounded-full ${OUTCOME_DOT[check.outcome]}`}
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">
          {check.protocol}{" "}
          <span className="text-muted-foreground text-2xs">{t(OUTCOME_LABEL[check.outcome])}</span>
        </p>
        {check.message ? <p className="text-muted-foreground text-xs">{check.message}</p> : null}
        {check.records.length > 0 ? (
          <details className="mt-1">
            <summary className="text-muted-foreground text-2xs cursor-pointer uppercase">
              {t("dns.records", { count: check.records.length })}
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

function suppressionList(t: Translate, entries: AstroliftEmailSuppressionEntry[]): ListDefinition {
  const reasons = [...new Set(entries.map((e) => e.reason).filter(Boolean))].sort();
  return emailList(
    t,
    "suppressions",
    [
      {
        key: "reason",
        label: t("suppression.reason"),
        options: reasons.map((r) => ({ value: r, label: r })),
      },
    ],
    t("suppression.search"),
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
  const t = useTranslations("appEmailService");
  const { formatDate } = useFormatters();
  const [def] = React.useState(() => suppressionList(t, detail.suppressionEntries));
  const list = useLocalListState(def);
  const page = selectRows(detail.suppressionEntries, pageOf(list), SUPPRESSION_SELECT);
  const columns: Column<AstroliftEmailSuppressionEntry>[] = [
    {
      id: "address",
      header: t("suppression.address"),
      sortKey: "address",
      cellClassName: "font-mono text-xs [overflow-wrap:anywhere]",
      cell: (entry) => entry.address,
    },
    {
      id: "reason",
      header: t("suppression.reason"),
      sortKey: "reason",
      cell: (entry) => (
        <Badge variant="outline" className="text-2xs">
          {entry.reason}
        </Badge>
      ),
    },
    {
      id: "date",
      header: t("suppression.date"),
      sortKey: "date",
      cellClassName: "text-muted-foreground text-xs",
      cell: (entry) => formatDate(entry.suppressedAt),
    },
    {
      id: "remove",
      header: <span className="sr-only">{t("suppression.remove")}</span>,
      label: t("suppression.remove"),
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
                <span className="sr-only">{t("suppression.removeSr")}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t("suppression.unsuppressTooltip")}</TooltipContent>
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
        title={t("suppression.title")}
        message={t("suppression.unsupported")}
        icon={<TrashIcon className="size-4" />}
      />
    );
  }

  return (
    <Card className="p-4">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
        {t("suppression.title")}
        <Badge variant="secondary" className="text-2xs">
          {detail.suppressionEntries.length}
        </Badge>
      </h3>

      <Can permission="managed_service.update">
        <div className="bg-muted/30 mb-3 rounded-md border p-3">
          <p className="text-muted-foreground text-2xs mb-2 uppercase">
            {t("suppression.addManual")}
          </p>
          <div className="space-y-2">
            <div>
              <Label htmlFor="suppress-address" className="text-xs">
                {t("suppression.address")}
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
                {t("suppression.note")}
              </Label>
              <Input
                id="suppress-note"
                value={addNote}
                onChange={(e) => setAddNote(e.target.value)}
                placeholder={t("suppression.notePlaceholder")}
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
              {t("suppression.suppress")}
            </Button>
          </div>
        </div>
      </Can>

      {detail.suppressionEntries.length === 0 ? (
        <p className="text-muted-foreground py-4 text-center text-xs">{t("suppression.empty")}</p>
      ) : (
        <ListPage<AstroliftEmailSuppressionEntry>
          embedded
          list={list}
          label={t("suppression.listLabel")}
          columns={columns}
          rows={page.rows}
          getRowId={suppressionKey}
          totalCount={page.totalCount}
          empty={{ icon: <TrashIcon className="size-5" />, title: t("suppression.empty") }}
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
            <AlertDialogTitle>
              {t("suppression.confirmTitle", { address: removalTarget?.address ?? "" })}
            </AlertDialogTitle>
            <AlertDialogDescription>{t("suppression.confirmBody")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removing}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              disabled={removing}
              onClick={() => {
                if (removalTarget) {
                  void handleRemove(removalTarget);
                }
              }}
            >
              {removing ? <Loader2Icon className="mr-1.5 size-3 animate-spin" /> : null}
              {t("suppression.unsuppress")}
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
  messageKey: string;
  placeholder: string;
}> = [
  { key: "from_name", messageKey: "fromName", placeholder: "Pulse Notifications" },
  { key: "reply_to", messageKey: "replyTo", placeholder: "support@example.com" },
  { key: "return_path", messageKey: "returnPath", placeholder: "bounce@example.com" },
  { key: "production_from", messageKey: "productionFrom", placeholder: "noreply@example.com" },
  { key: "preview_from", messageKey: "previewFrom", placeholder: "preview@example.com" },
];

export function SenderConfigPanelView({
  serviceConfig,
  saving: loading,
  onSave,
}: ReturnType<typeof useSenderConfig>) {
  const t = useTranslations("appEmailService");
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
          {t("sender.title")}
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
                {t("cancel")}
              </Button>
              <Button size="sm" disabled={loading || !dirty} onClick={() => void handleSave()}>
                {loading ? <Loader2Icon className="mr-1.5 size-3 animate-spin" /> : null}
                {t("save")}
              </Button>
            </div>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              <PencilIcon className="mr-1 size-3" />
              {t("edit")}
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
                {t(`sender.fields.${f.messageKey}.label`)}
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
                    <span className="text-muted-foreground italic">{t("sender.notSet")}</span>
                  )}
                </p>
              )}
              {editing ? (
                <p className="text-muted-foreground text-2xs">
                  {t(`sender.fields.${f.messageKey}.hint`)}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ── SNS event publishing (#636) ────────────────────────────────────

function SnsEventPublishingPanel({ serviceConfig }: { serviceConfig: Record<string, unknown> }) {
  const t = useTranslations("appEmailService");
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
          {t("sns.title")}
        </h3>
        <Badge
          variant="outline"
          className={
            enabled
              ? "bg-success/10 text-success-fg"
              : "border-warning-border bg-warning/10 text-warning-fg"
          }
        >
          {enabled ? t("sns.active") : t("sns.notConfigured")}
        </Badge>
      </div>
      {enabled ? (
        <>
          <p className="text-muted-foreground mb-2 text-xs">{t("sns.activeBody")}</p>
          <div className="bg-muted/40 text-2xs mb-2 flex items-center gap-2 rounded-md border p-2 font-mono">
            <code className="flex-1 truncate" title={arn}>
              {arn}
            </code>
            <Button
              variant="ghost"
              size="icon"
              className="size-6 shrink-0"
              onClick={() => copyToClipboard(arn, t("copied.topicArn"), t("copyFailed"))}
            >
              <CopyIcon className="size-3" />
            </Button>
          </div>
          {configSet ? (
            <p className="text-muted-foreground text-2xs">
              {t.rich("sns.configSet", {
                name: configSet,
                code: (chunks) => <code className="text-foreground font-mono">{chunks}</code>,
              })}
            </p>
          ) : null}
        </>
      ) : (
        <div className="text-muted-foreground space-y-2 text-xs">
          <p>{t("sns.missing")}</p>
          <p>
            {t.rich("sns.howTo", {
              env: "ASTROLIFT_SES_EVENTS_SNS_TOPIC_ARN",
              code: (chunks) => <code className="text-foreground font-mono">{chunks}</code>,
            })}
          </p>
        </div>
      )}
    </Card>
  );
}

// ── Sender alert rules (#627, #639) ────────────────────────────────

const SES_ALERT_KINDS: ReadonlyArray<{
  value: "ses_bounce_rate" | "ses_complaint_rate";
  messageKey: string;
  defaultThreshold: number;
  unit: string;
}> = [
  {
    value: "ses_bounce_rate",
    messageKey: "bounce",
    defaultThreshold: 5,
    unit: "%",
  },
  {
    value: "ses_complaint_rate",
    messageKey: "complaint",
    defaultThreshold: 0.1,
    unit: "%",
  },
];

const SEVERITY_OPTIONS = ["critical", "warning", "info"] as const;

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

const alertRulesList = (t: Translate) =>
  emailList(
    t,
    "alert-rules",
    [
      {
        key: "severity",
        label: t("alerts.severity"),
        options: SEVERITY_OPTIONS.map((value) => ({
          value,
          label: t(`alerts.severities.${value}`),
        })),
      },
      {
        key: "status",
        label: t("alerts.status"),
        options: [
          { value: "active", label: t("alerts.active") },
          { value: "inactive", label: t("alerts.inactive") },
        ],
      },
    ],
    t("alerts.search"),
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
  const t = useTranslations("appEmailService");
  const [def] = React.useState(() => alertRulesList(t));
  const list = useLocalListState(def);
  const page = selectRows(rules, pageOf(list), ALERT_RULES_SELECT);
  const columns: Column<AlertRuleRow>[] = [
    {
      id: "name",
      header: t("alerts.name"),
      sortKey: "name",
      cellClassName: "text-xs [overflow-wrap:anywhere]",
      cell: (r) => r.name,
    },
    {
      id: "condition",
      header: t("alerts.condition"),
      cellClassName: "text-muted-foreground text-2xs font-mono [overflow-wrap:anywhere]",
      cell: (r) => predicateLabel(r.predicate),
    },
    {
      id: "severity",
      header: t("alerts.severity"),
      sortKey: "severity",
      cell: (r) => (
        <Badge variant="outline" className={`capitalize ${severityBadge(r.severity)}`}>
          {(SEVERITY_OPTIONS as readonly string[]).includes(r.severity)
            ? t(`alerts.severities.${r.severity}`)
            : r.severity}
        </Badge>
      ),
    },
    {
      id: "status",
      header: t("alerts.status"),
      sortKey: "status",
      cellClassName: "text-xs",
      cell: (r) => (r.isActive ? t("alerts.active") : t("alerts.inactive")),
    },
    {
      id: "delete",
      header: <span className="sr-only">{t("delete")}</span>,
      label: t("delete"),
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
            <span className="sr-only">{t("alerts.deleteSr")}</span>
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
          {t("sections.alerts")}
          <Badge variant="secondary" className="text-2xs">
            {rules.length}
          </Badge>
        </h3>
      </div>
      {loading && rules.length === 0 ? (
        <Skeleton className="h-12 w-full" />
      ) : rules.length === 0 ? (
        <p className="text-muted-foreground mb-3 text-xs">{t("alerts.emptyBody")}</p>
      ) : (
        <ListPage<AlertRuleRow>
          embedded
          list={list}
          label={t("sections.alerts")}
          columns={columns}
          rows={page.rows}
          getRowId={(r) => r.id}
          totalCount={page.totalCount}
          empty={{ icon: <BellIcon className="size-5" />, title: t("alerts.emptyTitle") }}
        />
      )}

      <Can permission="managed_service.update">
        <div className="bg-muted/30 mt-3 rounded-md border p-3">
          <p className="text-muted-foreground text-2xs mb-2 uppercase">{t("alerts.add")}</p>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs">{t("alerts.condition")}</Label>
              <Select value={kind} onValueChange={(v) => setKind(v as SesAlertKind)}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SES_ALERT_KINDS.map((k) => (
                    <SelectItem key={k.value} value={k.value}>
                      {t(`alerts.kinds.${k.messageKey}.label`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">{t("alerts.threshold")}</Label>
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
              <Label className="text-xs">{t("alerts.severity")}</Label>
              <Select value={severity} onValueChange={setSeverity}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEVERITY_OPTIONS.map((s) => (
                    <SelectItem key={s} value={s}>
                      {t(`alerts.severities.${s}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">{t("alerts.nameOptional")}</Label>
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
            {t("alerts.create")}
          </Button>
          <p className="text-muted-foreground text-2xs mt-2">
            {t(
              `alerts.kinds.${SES_ALERT_KINDS.find((k) => k.value === kind)?.messageKey ?? "bounce"}.hint`
            )}
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
  const t = useTranslations("appEmailService.engagement");
  const fmt = useFormatter();
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
          label: t("openRate"),
          pct: m.openRatePct,
          count: m.totalOpens,
          denomLabel: "ofDeliveries",
          denom: m.totalDeliveries,
          direction: "higher_better",
        },
        {
          label: t("clickRate"),
          pct: m.clickRatePct,
          count: m.totalClicks,
          denomLabel: "ofDeliveries",
          denom: m.totalDeliveries,
          direction: "higher_better",
        },
        {
          label: t("bounceRate"),
          pct: m.bounceRatePct,
          count: m.totalBounces,
          denomLabel: "ofSends",
          denom: m.totalSends,
          warnAt: 5,
          critAt: 10,
          direction: "lower_better",
        },
        {
          label: t("complaintRate"),
          pct: m.complaintRatePct,
          count: m.totalComplaints,
          denomLabel: "ofSends",
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
          {t("title")}
        </h3>
        {m ? (
          <span className="text-muted-foreground text-2xs">
            {t("lastDays", { days: m.windowDays })}
          </span>
        ) : null}
      </div>
      {!snsConfigured ? (
        <p className="text-muted-foreground mb-3 text-xs">{t("notConfigured")}</p>
      ) : null}
      {loading && !m ? (
        <div className="grid grid-cols-2 gap-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : !m || m.totalSends === 0 ? (
        <p className="text-muted-foreground text-xs">{t("empty")}</p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {tiles.map((tile) => {
            const tone =
              tile.direction === "lower_better"
                ? tile.critAt && tile.pct >= tile.critAt
                  ? "text-danger-fg"
                  : tile.warnAt && tile.pct >= tile.warnAt
                    ? "text-warning-fg"
                    : "text-success-fg"
                : "text-foreground";
            return (
              <div key={tile.label} className="bg-muted/30 rounded-md border p-3">
                <p className="text-muted-foreground text-2xs uppercase">{tile.label}</p>
                <p className={`font-mono text-2xl tabular-nums ${tone}`}>
                  {formatPct(tile.pct, 2)}
                </p>
                <p className="text-muted-foreground text-2xs">
                  {t(tile.denomLabel, {
                    count: formatNumber(fmt, tile.count),
                    total: formatNumber(fmt, tile.denom),
                  })}
                </p>
              </div>
            );
          })}
        </div>
      )}
      {m && m.totalSends > 0 ? (
        <p className="text-muted-foreground text-2xs mt-3">
          {t("summary", {
            sends: formatNumber(fmt, m.totalSends),
            delivered: formatNumber(fmt, m.totalDeliveries),
            days: m.windowDays,
          })}
        </p>
      ) : null}
    </Card>
  );
}

// ── Message log + bounce/complaint drill-in (#624, #625) ───────────

const EVENT_KIND_OPTIONS: ReadonlyArray<string> = [
  "delivery",
  "bounce",
  "complaint",
  "open",
  "click",
  "send",
  "reject",
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
const messagesList = (t: Translate) =>
  emailList(
    t,
    "messages",
    [
      {
        key: "event",
        label: t("messages.event"),
        options: EVENT_KIND_OPTIONS.map((value) => ({
          value,
          label: t(`messages.events.${value}`),
        })),
      },
      { key: "recipient", label: t("messages.recipient") },
    ],
    t("messages.search"),
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
  const t = useTranslations("appEmailService");
  const { formatDateTime } = useFormatters();
  const [def] = React.useState(() => messagesList(t));
  const list = useLocalListState(def, {
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
      header: t("messages.recipient"),
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
                aria-label={t("messages.detailsFor", { recipient: m.recipient })}
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
      header: t("messages.subject"),
      sortKey: "subject",
      cellClassName: "max-w-[14rem] truncate align-top text-xs",
      cell: (m) =>
        m.subject ? (
          <span title={m.subject}>{m.subject}</span>
        ) : (
          <span className="text-muted-foreground italic">{t("messages.noSubject")}</span>
        ),
    },
    {
      id: "event",
      header: t("messages.event"),
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
      header: t("messages.when"),
      sortKey: "when",
      cellClassName: "text-muted-foreground text-2xs align-top",
      cell: (m) => formatDateTime(m.occurredAt),
    },
  ];

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <InboxIcon className="size-4" />
          {t("messages.title")}
          <Badge variant="secondary" className="text-2xs">
            {messages.length}
          </Badge>
        </h3>
        <Button variant="ghost" size="sm" onClick={onRefresh} disabled={loading}>
          <RefreshCwIcon className={`mr-1 size-3 ${loading ? "animate-spin" : ""}`} />
          {t("messages.refresh")}
        </Button>
      </div>
      <ListPage<AstroliftEmailMessage>
        embedded
        list={list}
        label={t("sections.messages")}
        columns={columns}
        rows={page.rows}
        getRowId={(m) => m.id}
        totalCount={page.totalCount}
        loading={loading && messages.length === 0}
        stale={loading && messages.length > 0}
        empty={{
          icon: <InboxIcon className="size-5" />,
          title: t("messages.emptyTitle"),
          description: t("messages.emptyBody"),
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
  const t = useTranslations("appEmailService");
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
          aria-label={t("messages.copyId")}
          onClick={(e) => {
            e.stopPropagation();
            copyToClipboard(messageId, t("copied.messageId"), t("copyFailed"));
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
          {t("messages.rawMetadata")}
        </summary>
        <pre className="bg-background/60 text-2xs mt-1 max-h-48 overflow-auto rounded-md border p-2 font-mono break-all whitespace-pre-wrap">
          {JSON.stringify(metadata, null, 2)}
        </pre>
      </details>
    </div>
  );
}

// ── Template management (#635, #628) ───────────────────────────────

const templatesList = (t: Translate) =>
  emailList(t, "templates", [], t("templates.search"), [{ key: "name", dir: "asc" }]);

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
  const t = useTranslations("appEmailService");
  const { formatDate } = useFormatters();
  const [def] = React.useState(() => templatesList(t));
  const list = useLocalListState(def);
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
      header: t("templates.name"),
      sortKey: "name",
      cellClassName: "min-w-0",
      cell: (tpl) => (
        <div className="flex min-w-0 flex-col gap-2">
          <span className="font-mono text-xs [overflow-wrap:anywhere]">{tpl.name}</span>
          {statsOpen.has(tpl.name) ? (
            <div className="bg-muted/30 rounded-md p-3">{renderStats(tpl.name)}</div>
          ) : null}
        </div>
      ),
    },
    {
      id: "subject",
      header: t("templates.subject"),
      sortKey: "subject",
      cellClassName: "max-w-[14rem] truncate align-top text-xs",
      cell: (tpl) => <span title={tpl.subject}>{tpl.subject}</span>,
    },
    {
      id: "created",
      header: t("templates.created"),
      sortKey: "created",
      cellClassName: "text-muted-foreground text-2xs align-top",
      cell: (tpl) => (tpl.createdAt ? formatDate(tpl.createdAt) : t("templates.unknown")),
    },
    {
      id: "actions",
      header: <span className="sr-only">{t("templates.actions")}</span>,
      label: t("templates.actions"),
      width: "w-28",
      align: "right",
      cellClassName: "align-top",
      cell: (tpl) => (
        <div className="inline-flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={() => toggleStats(tpl.name)}
            title={t("templates.toggleStats")}
            aria-expanded={statsOpen.has(tpl.name)}
          >
            <BarChart3Icon className="size-3.5" />
            <span className="sr-only">{t("templates.stats")}</span>
          </Button>
          <Can permission="managed_service.update">
            <Button variant="ghost" size="icon" className="size-7" onClick={() => beginEdit(tpl)}>
              <PencilIcon className="size-3.5" />
              <span className="sr-only">{t("edit")}</span>
            </Button>
          </Can>
          <Can permission="managed_service.update">
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => setDeleteTarget(tpl)}
              disabled={deleting}
            >
              <TrashIcon className="size-3.5" />
              <span className="sr-only">{t("delete")}</span>
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
          {t("sections.templates")}
          <Badge variant="secondary" className="text-2xs">
            {templates.length}
          </Badge>
        </h3>
        <Can permission="managed_service.update">
          {!formOpen ? (
            <Button size="sm" variant="ghost" onClick={beginCreate}>
              <PlusIcon className="mr-1 size-3" />
              {t("templates.new")}
            </Button>
          ) : null}
        </Can>
      </div>

      {formOpen ? (
        <div className="bg-muted/30 mb-3 space-y-2 rounded-md border p-3">
          <p className="text-muted-foreground text-2xs uppercase">
            {creatingNew ? t("templates.new") : t("templates.editing", { name: editingName ?? "" })}
          </p>
          <div className="space-y-1">
            <Label className="text-xs">{t("templates.name")}</Label>
            <Input
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              placeholder="welcome_email"
              disabled={!creatingNew || busy}
              className="font-mono text-xs"
            />
            {!creatingNew ? (
              <p className="text-muted-foreground text-2xs">{t("templates.nameFixed")}</p>
            ) : null}
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t("templates.subject")}</Label>
            <Input
              value={draft.subject}
              onChange={(e) => setDraft((d) => ({ ...d, subject: e.target.value }))}
              placeholder="Welcome to {{app_name}}"
              disabled={busy}
              className="text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t("templates.htmlBody")}</Label>
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
            <Label className="text-xs">{t("templates.textBody")}</Label>
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
              {t("cancel")}
            </Button>
            <Button size="sm" disabled={busy} onClick={() => void handleSave()}>
              {busy ? <Loader2Icon className="mr-1.5 size-3 animate-spin" /> : null}
              {creatingNew ? t("templates.create") : t("templates.saveChanges")}
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
        <p className="text-muted-foreground py-3 text-center text-xs">{t("templates.emptyBody")}</p>
      ) : (
        <ListPage<AstroliftEmailTemplate>
          embedded
          list={list}
          label={t("sections.templates")}
          columns={columns}
          rows={page.rows}
          getRowId={(tpl) => tpl.name}
          totalCount={page.totalCount}
          empty={{ icon: <FileTextIcon className="size-5" />, title: t("templates.emptyTitle") }}
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
            <AlertDialogTitle>
              {t("templates.deleteTitle", { name: deleteTarget?.name ?? "" })}
            </AlertDialogTitle>
            <AlertDialogDescription>{t("templates.deleteBody")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction disabled={deleting} onClick={() => void handleDelete()}>
              {deleting ? <Loader2Icon className="mr-1.5 size-3 animate-spin" /> : null}
              {t("templates.deleteConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

export function TemplateStatsView({ name, points, loading }: ReturnType<typeof useTemplateStats>) {
  const t = useTranslations("appEmailService.stats");
  const fmt = useFormatter();
  const { formatDate } = useFormatters();
  if (loading && points.length === 0) {
    return <Skeleton className="h-16 w-full" />;
  }
  if (points.length === 0) {
    return <p className="text-muted-foreground text-xs">{t("noActivity", { name })}</p>;
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
                  <div>{formatDate(p.timestamp)}</div>
                  <div>{t("day", { sends: p.sends, delivered: p.deliveries })}</div>
                  {p.bounces > 0 ? <div>{t("bounces", { count: p.bounces })}</div> : null}
                  {p.complaints > 0 ? <div>{t("complaints", { count: p.complaints })}</div> : null}
                </div>
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
      <p className="text-muted-foreground text-2xs">
        {t("summary", {
          sends: formatNumber(fmt, totals.sends),
          delivered: formatNumber(fmt, totals.deliveries),
          bounces: formatNumber(fmt, totals.bounces),
          complaints: formatNumber(fmt, totals.complaints),
        })}
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
