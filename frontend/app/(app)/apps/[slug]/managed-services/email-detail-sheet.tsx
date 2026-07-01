"use client";

/**
 * Email-service detail sheet (#629, #631, #632, #633, #634).
 *
 * Renders the SES (or future GCP/Azure) observability surface for one
 * bound email managed-service. Five panels:
 *
 *   1. Sender reputation / sandbox vs production (#633)
 *   2. Send-rate vs quota tile (#629)
 *   3. Identity verification badge with DKIM token list (#634)
 *   4. DKIM / SPF / DMARC DNS auth status (#632)
 *   5. Suppression list view + manage (#631)
 *
 * Backends that don't expose any of these (GCP, Azure) populate
 * `unsupportedNotes`; the UI shades the corresponding panel and shows
 * the upstream's hint instead of the data.
 *
 * The sheet renders inside the managed-services page so operators stay
 * in flow when debugging deliverability — no extra navigation.
 */

import { useMutation, useQuery } from "@apollo/client/react";
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
import { toast } from "sonner";

import { Can } from "@/components/Can";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { GET_COST_BY_BINDING } from "@/graphql/billing/billing.queries";
import type { AstroliftCostAttribution } from "@/graphql/billing/billing.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  CREATE_ALERT_RULE,
  DELETE_ALERT_RULE,
  LIST_ALERT_RULES,
} from "@/graphql/operations/alerts.queries";
import {
  ADD_EMAIL_SUPPRESSION_ENTRY,
  CREATE_EMAIL_TEMPLATE,
  DELETE_EMAIL_TEMPLATE,
  REMOVE_EMAIL_SUPPRESSION_ENTRY,
  UPDATE_EMAIL_TEMPLATE,
  UPDATE_MANAGED_SERVICE,
} from "@/graphql/services/services.mutations";
import {
  GET_EMAIL_ENGAGEMENT_METRICS,
  GET_EMAIL_MESSAGES,
  GET_EMAIL_SERVICE_DETAIL,
  GET_EMAIL_TEMPLATE_STATS,
  GET_EMAIL_TEMPLATES,
} from "@/graphql/services/services.queries";
import type {
  AstroliftEmailDnsAuthCheck,
  AstroliftEmailEngagementMetrics,
  AstroliftEmailMessage,
  AstroliftEmailServiceDetail,
  AstroliftEmailSuppressionEntry,
  AstroliftEmailTemplate,
  AstroliftTemplateSendStatPoint,
  EmailDnsCheckOutcome,
} from "@/graphql/services/services.types";

interface DetailResp {
  astroliftEmailServiceDetail: AstroliftEmailServiceDetail | null;
}

interface CostByBindingResp {
  astroliftCostByBinding: AstroliftCostAttribution;
}

interface AddResp {
  addEmailSuppressionEntry: MutationResult<{ address: string; reason: string }>;
}

interface RemoveResp {
  removeEmailSuppressionEntry: MutationResult<{
    address: string;
    removed: boolean;
  }>;
}

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

interface EmailDetailSheetProps {
  managedServiceId: string;
  serviceName: string;
  appSlug: string;
  serviceConfig: Record<string, unknown>;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}

export function EmailDetailSheet({
  managedServiceId,
  serviceName,
  appSlug,
  serviceConfig,
  open,
  onOpenChange,
}: EmailDetailSheetProps) {
  const { data, loading, refetch } = useQuery<DetailResp>(GET_EMAIL_SERVICE_DETAIL, {
    variables: { managedServiceId },
    skip: !open,
    fetchPolicy: "cache-and-network",
  });
  const detail = data?.astroliftEmailServiceDetail ?? null;

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

        {loading && !detail ? (
          <div className="space-y-4 p-1">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        ) : detail ? (
          <div className="space-y-6 p-1">
            <ReputationPanel detail={detail} />
            <QuotaPanel detail={detail} />
            <CostPanel managedServiceId={detail.managedServiceId} appSlug={appSlug} open={open} />
            <IdentityPanel detail={detail} />
            <DnsAuthPanel detail={detail} />
            <SuppressionPanel
              detail={detail}
              onRefetch={() => {
                void refetch();
              }}
            />
            <SenderConfigPanel managedServiceId={managedServiceId} serviceConfig={serviceConfig} />
            <SnsEventPublishingPanel serviceConfig={serviceConfig} />
            <SenderAlertRulesPanel managedServiceId={managedServiceId} />
            <EngagementMetricsPanel
              managedServiceId={managedServiceId}
              snsConfigured={
                typeof serviceConfig.sns_event_destination_arn === "string" &&
                (serviceConfig.sns_event_destination_arn as string).length > 0
              }
            />
            <MessageLogPanel managedServiceId={managedServiceId} />
            <TemplateManagementPanel managedServiceId={managedServiceId} />
            {detail.unsupportedNotes.length > 0 ? (
              <Card className="bg-muted/40 p-3">
                <p className="text-muted-foreground mb-1 text-2xs font-semibold uppercase">
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
          <AlertTriangleIcon className="size-3.5 shrink-0 text-warning-fg" />
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
              className={`mt-1 text-2xs ${
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

function CostPanel({
  managedServiceId,
  appSlug,
  open,
}: {
  managedServiceId: string;
  appSlug: string;
  open: boolean;
}) {
  const mtdQuery = useQuery<CostByBindingResp>(GET_COST_BY_BINDING, {
    variables: { window: "MTD", registeredAppSlug: appSlug },
    skip: !open || !appSlug,
    fetchPolicy: "cache-and-network",
  });
  const trailingQuery = useQuery<CostByBindingResp>(GET_COST_BY_BINDING, {
    variables: { days: 30, registeredAppSlug: appSlug },
    skip: !open || !appSlug,
    fetchPolicy: "cache-and-network",
  });

  const mtdSum = React.useMemo(
    () => sumForService(mtdQuery.data, managedServiceId),
    [mtdQuery.data, managedServiceId]
  );
  const trailingSum = React.useMemo(
    () => sumForService(trailingQuery.data, managedServiceId),
    [trailingQuery.data, managedServiceId]
  );

  const currency =
    mtdQuery.data?.astroliftCostByBinding?.currency ??
    trailingQuery.data?.astroliftCostByBinding?.currency ??
    "USD";

  const loading = mtdQuery.loading || trailingQuery.loading;
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

function sumForService(
  data: CostByBindingResp | undefined,
  managedServiceId: string
): number | null {
  const rows = data?.astroliftCostByBinding?.attributedRows;
  if (!rows) return null;
  const matches = rows.filter((r) => r.managedServiceId === managedServiceId);
  if (matches.length === 0) return 0;
  return matches.reduce((sum, r) => sum + r.amountCents, 0);
}

// ── Identity verification badge (#634) ─────────────────────────────

function IdentityPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
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
      {iv.verificationToken ? (
        <div className="bg-muted/40 mb-3 rounded-md border p-2 font-mono text-2xs">
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
              className="bg-muted/40 flex items-center gap-2 rounded-md border p-2 font-mono text-2xs"
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
      <p className="text-muted-foreground mt-3 text-2xs">
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
            <summary className="text-muted-foreground cursor-pointer text-2xs uppercase">
              {check.records.length} record{check.records.length > 1 ? "s" : ""}
            </summary>
            <ul className="mt-1 space-y-0.5">
              {check.records.map((r) => (
                <li key={r} className="bg-muted/40 rounded p-1.5 font-mono text-2xs break-all">
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

function SuppressionPanel({
  detail,
  onRefetch,
}: {
  detail: AstroliftEmailServiceDetail;
  onRefetch: () => void;
}) {
  const [addAddress, setAddAddress] = React.useState("");
  const [addNote, setAddNote] = React.useState("");
  const [removalTarget, setRemovalTarget] = React.useState<AstroliftEmailSuppressionEntry | null>(
    null
  );

  const [addEntry, { loading: adding }] = useMutation<AddResp>(ADD_EMAIL_SUPPRESSION_ENTRY, {
    refetchQueries: [
      {
        query: GET_EMAIL_SERVICE_DETAIL,
        variables: { managedServiceId: detail.managedServiceId },
      },
    ],
    awaitRefetchQueries: true,
  });

  const [removeEntry, { loading: removing }] = useMutation<RemoveResp>(
    REMOVE_EMAIL_SUPPRESSION_ENTRY,
    {
      refetchQueries: [
        {
          query: GET_EMAIL_SERVICE_DETAIL,
          variables: { managedServiceId: detail.managedServiceId },
        },
      ],
      awaitRefetchQueries: true,
    }
  );

  async function handleAdd() {
    const address = addAddress.trim();
    if (!address) return;
    try {
      const { data } = await addEntry({
        variables: {
          input: {
            managedServiceId: detail.managedServiceId,
            address,
            reason: "MANUAL",
            note: addNote.trim(),
          },
        },
      });
      const env = data?.addEmailSuppressionEntry;
      if (!env) {
        toast.error("No response from server");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Suppression add failed");
        return;
      }
      toast.success(`Suppressed ${address}`);
      setAddAddress("");
      setAddNote("");
      onRefetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Suppression add failed");
    }
  }

  async function handleRemove(entry: AstroliftEmailSuppressionEntry) {
    try {
      const { data } = await removeEntry({
        variables: {
          input: {
            managedServiceId: detail.managedServiceId,
            address: entry.address,
          },
        },
      });
      const env = data?.removeEmailSuppressionEntry;
      if (!env) {
        toast.error("No response from server");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Suppression remove failed");
        return;
      }
      if (env.data?.removed) {
        toast.success(`Removed ${entry.address} from suppression list`);
      } else {
        toast.info(`${entry.address} was already removed`);
      }
      setRemovalTarget(null);
      onRefetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Suppression remove failed");
    }
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
          <p className="text-muted-foreground mb-2 text-2xs uppercase">Add manual entry</p>
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
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Address</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead>Date</TableHead>
              <TableHead className="w-12 text-right"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {detail.suppressionEntries.map((entry) => (
              <TableRow key={entry.address}>
                <TableCell className="font-mono text-xs">{entry.address}</TableCell>
                <TableCell>
                  <Badge variant="outline" className="text-2xs">
                    {entry.reason}
                  </Badge>
                </TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {new Date(entry.suppressedAt).toLocaleDateString()}
                </TableCell>
                <TableCell className="text-right">
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
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
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

interface UpdateManagedServiceResp {
  updateManagedService: MutationResult<{ id: string }>;
}

function SenderConfigPanel({
  managedServiceId,
  serviceConfig,
}: {
  managedServiceId: string;
  serviceConfig: Record<string, unknown>;
}) {
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

  const [updateSvc, { loading }] = useMutation<UpdateManagedServiceResp>(UPDATE_MANAGED_SERVICE);

  const dirty = SENDER_FIELDS.some((f) => (values[f.key] ?? "") !== initial[f.key]);

  async function handleSave() {
    const nextConfig: Record<string, unknown> = { ...serviceConfig };
    for (const f of SENDER_FIELDS) {
      const v = (values[f.key] ?? "").trim();
      if (v) nextConfig[f.key] = v;
      else delete nextConfig[f.key];
    }
    try {
      const { data } = await updateSvc({
        variables: {
          input: { id: managedServiceId, config: nextConfig },
        },
      });
      const env = data?.updateManagedService;
      if (!env) {
        toast.error("No response from server");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Update failed");
        return;
      }
      toast.success("Sender config saved");
      setEditing(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed");
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
            platform&apos;s ingester via SNS. The recent-message log and engagement metrics below stay
            live.
          </p>
          <div className="bg-muted/40 mb-2 flex items-center gap-2 rounded-md border p-2 font-mono text-2xs">
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
            SNS event publishing is not configured for this identity. Without it the platform can&apos;t
            render the per-message log, bounce drill-in, or open/click engagement.
          </p>
          <p>
            Set{" "}
            <code className="text-foreground font-mono">ASTROLIFT_SES_EVENTS_SNS_TOPIC_ARN</code> in
            the install&apos;s environment config and re-provision this managed service to wire the SES
            configuration set, SNS topic, and platform subscription.
          </p>
        </div>
      )}
    </Card>
  );
}

// ── Sender alert rules (#627, #639) ────────────────────────────────

interface AlertRuleRow {
  id: string;
  name: string;
  target: string;
  targetId: string;
  managedServiceId: string | null;
  severity: string;
  predicate: Record<string, unknown>;
  notifyChannels: unknown;
  isActive: boolean;
  createdAt: string;
}

interface AlertRulesResp {
  astroliftAlertRules: AlertRuleRow[];
}

interface CreateAlertRuleResp {
  createAlertRule: MutationResult<AlertRuleRow>;
}

interface DeleteAlertRuleResp {
  deleteAlertRule: MutationResult<{ id: string; deleted: boolean }>;
}

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

function SenderAlertRulesPanel({ managedServiceId }: { managedServiceId: string }) {
  const queryVars = React.useMemo(
    () => ({
      target: "managed_service",
      targetId: managedServiceId,
      activeOnly: false,
    }),
    [managedServiceId]
  );
  const { data, loading } = useQuery<AlertRulesResp>(LIST_ALERT_RULES, {
    variables: queryVars,
    fetchPolicy: "cache-and-network",
  });
  const refetchQueries = React.useMemo(
    () => [{ query: LIST_ALERT_RULES, variables: queryVars }],
    [queryVars]
  );

  const [createRule, { loading: creating }] = useMutation<CreateAlertRuleResp>(CREATE_ALERT_RULE, {
    refetchQueries,
    awaitRefetchQueries: true,
  });
  const [deleteRule, { loading: deleting }] = useMutation<DeleteAlertRuleResp>(DELETE_ALERT_RULE, {
    refetchQueries,
    awaitRefetchQueries: true,
  });

  const [kind, setKind] = React.useState<"ses_bounce_rate" | "ses_complaint_rate">(
    "ses_bounce_rate"
  );
  const [threshold, setThreshold] = React.useState<string>("5");
  const [severity, setSeverity] = React.useState<string>("warning");
  const [name, setName] = React.useState("");

  React.useEffect(() => {
    const preset = SES_ALERT_KINDS.find((k) => k.value === kind);
    if (preset) setThreshold(String(preset.defaultThreshold));
  }, [kind]);

  const rules = data?.astroliftAlertRules ?? [];

  async function handleCreate() {
    const n = Number(threshold);
    if (!Number.isFinite(n) || n <= 0) {
      toast.error("Threshold must be a positive number");
      return;
    }
    const ruleName = name.trim() || defaultAlertName(kind, n);
    try {
      const { data } = await createRule({
        variables: {
          input: {
            name: ruleName,
            target: "managed_service",
            targetId: managedServiceId,
            managedServiceId,
            severity,
            predicate: { kind, threshold_pct: n },
            notifyChannels: [{ kind: "in_app", ref: "" }],
            isActive: true,
          },
        },
      });
      const env = data?.createAlertRule;
      if (!env) {
        toast.error("No response from server");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Create alert failed");
        return;
      }
      toast.success(`Created ${ruleName}`);
      setName("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Create alert failed");
    }
  }

  async function handleDelete(rule: AlertRuleRow) {
    try {
      const { data } = await deleteRule({
        variables: { input: { id: rule.id } },
      });
      const env = data?.deleteAlertRule;
      if (!env) return;
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Delete alert failed");
        return;
      }
      toast.success(`Deleted ${rule.name}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete alert failed");
    }
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
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Condition</TableHead>
              <TableHead>Severity</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="w-10 text-right" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rules.map((r) => (
              <TableRow key={r.id}>
                <TableCell className="text-xs">{r.name}</TableCell>
                <TableCell className="text-muted-foreground font-mono text-2xs">
                  {predicateLabel(r.predicate)}
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className={`capitalize ${severityBadge(r.severity)}`}>
                    {r.severity}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">{r.isActive ? "Active" : "Inactive"}</TableCell>
                <TableCell className="text-right">
                  <Can permission="managed_service.update">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7"
                      disabled={deleting}
                      onClick={() => void handleDelete(r)}
                    >
                      <TrashIcon className="size-3.5" />
                      <span className="sr-only">Delete alert rule</span>
                    </Button>
                  </Can>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Can permission="managed_service.update">
        <div className="bg-muted/30 mt-3 rounded-md border p-3">
          <p className="text-muted-foreground mb-2 text-2xs uppercase">Add alert</p>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs">Condition</Label>
              <Select
                value={kind}
                onValueChange={(v) => setKind(v as "ses_bounce_rate" | "ses_complaint_rate")}
              >
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
          <p className="text-muted-foreground mt-2 text-2xs">
            {SES_ALERT_KINDS.find((k) => k.value === kind)?.hint}
          </p>
        </div>
      </Can>
    </Card>
  );
}

function defaultAlertName(
  kind: "ses_bounce_rate" | "ses_complaint_rate",
  threshold: number
): string {
  const label = kind === "ses_bounce_rate" ? "Bounce rate" : "Complaint rate";
  return `${label} > ${threshold}%`;
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

interface EngagementResp {
  astroliftEmailEngagementMetrics: AstroliftEmailEngagementMetrics | null;
}

function EngagementMetricsPanel({
  managedServiceId,
  snsConfigured,
}: {
  managedServiceId: string;
  snsConfigured: boolean;
}) {
  const { data, loading } = useQuery<EngagementResp>(GET_EMAIL_ENGAGEMENT_METRICS, {
    variables: { managedServiceId, days: 30 },
    fetchPolicy: "cache-and-network",
  });
  const m = data?.astroliftEmailEngagementMetrics ?? null;

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
        <p className="text-muted-foreground mt-3 text-2xs">
          {formatNumber(m.totalSends)} sends · {formatNumber(m.totalDeliveries)} delivered · last{" "}
          {m.windowDays}d
        </p>
      ) : null}
    </Card>
  );
}

// ── Message log + bounce/complaint drill-in (#624, #625) ───────────

interface MessagesResp {
  astroliftEmailMessages: AstroliftEmailMessage[];
}

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

function MessageLogPanel({ managedServiceId }: { managedServiceId: string }) {
  const [eventKind, setEventKind] = React.useState<string>("");
  const [recipient, setRecipient] = React.useState<string>("");
  const [appliedRecipient, setAppliedRecipient] = React.useState<string>("");
  const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set<string>());

  const { data, loading, refetch } = useQuery<MessagesResp>(GET_EMAIL_MESSAGES, {
    variables: {
      managedServiceId,
      limit: 50,
      eventKind: eventKind || null,
      recipient: appliedRecipient || null,
    },
    fetchPolicy: "cache-and-network",
  });

  const messages = data?.astroliftEmailMessages ?? [];

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

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
        <Button variant="ghost" size="sm" onClick={() => void refetch()} disabled={loading}>
          <RefreshCwIcon className={`mr-1 size-3 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>
      <div className="mb-3 grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-xs">Event</Label>
          <Select
            value={eventKind === "" ? "__all__" : eventKind}
            onValueChange={(v) => setEventKind(v === "__all__" ? "" : v)}
          >
            <SelectTrigger className="h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {EVENT_KIND_OPTIONS.map((o) => (
                <SelectItem key={o.value || "__all__"} value={o.value || "__all__"}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Recipient</Label>
          <Input
            value={recipient}
            onChange={(e) => setRecipient(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") setAppliedRecipient(recipient.trim());
            }}
            onBlur={() => setAppliedRecipient(recipient.trim())}
            placeholder="user@example.com"
            className="h-8 font-mono text-xs"
          />
        </div>
      </div>
      {loading && messages.length === 0 ? (
        <div className="space-y-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : messages.length === 0 ? (
        <p className="text-muted-foreground py-4 text-center text-xs">
          No messages match the current filter. Either nothing has been sent in the retention window
          or SNS event publishing isn&apos;t configured.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Recipient</TableHead>
              <TableHead>Subject</TableHead>
              <TableHead>Event</TableHead>
              <TableHead>When</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {messages.map((m) => {
              const isOpen = expanded.has(m.id);
              const hasMetadata = m.metadata && Object.keys(m.metadata).length > 0;
              return (
                <React.Fragment key={m.id}>
                  <TableRow
                    className={hasMetadata ? "cursor-pointer" : undefined}
                    onClick={() => {
                      if (hasMetadata) toggleExpanded(m.id);
                    }}
                  >
                    <TableCell className="w-8">
                      {hasMetadata ? (
                        isOpen ? (
                          <ChevronDownIcon className="size-3.5" />
                        ) : (
                          <ChevronRightIcon className="size-3.5" />
                        )
                      ) : null}
                    </TableCell>
                    <TableCell className="font-mono text-2xs">{m.recipient}</TableCell>
                    <TableCell className="max-w-[14rem] truncate text-xs" title={m.subject}>
                      {m.subject || (
                        <span className="text-muted-foreground italic">(no subject)</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={`text-2xs ${eventKindBadge(m.eventKind)}`}
                      >
                        {m.eventKind}
                      </Badge>
                    </TableCell>
                    <TableCell
                      className="text-muted-foreground text-2xs"
                      title={new Date(m.occurredAt).toLocaleString()}
                    >
                      {new Date(m.occurredAt).toLocaleString()}
                    </TableCell>
                  </TableRow>
                  {isOpen && hasMetadata ? (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={5} className="bg-muted/30">
                        <MessageMetadata
                          eventKind={m.eventKind}
                          messageId={m.messageId}
                          metadata={m.metadata}
                        />
                      </TableCell>
                    </TableRow>
                  ) : null}
                </React.Fragment>
              );
            })}
          </TableBody>
        </Table>
      )}
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
      <div className="flex items-center gap-2 font-mono text-2xs">
        <span className="text-muted-foreground">message-id</span>
        <code className="text-foreground truncate" title={messageId}>
          {messageId}
        </code>
        <Button
          variant="ghost"
          size="icon"
          className="size-5"
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
          <MailWarningIcon className="size-3.5 text-danger-fg" />
          <span>
            <strong>{bounceType ?? "bounce"}</strong>
            {bounceSubType ? ` · ${bounceSubType}` : ""}
          </span>
        </div>
      ) : null}
      {eventKind === "complaint" && complaintType ? (
        <div className="flex items-center gap-2">
          <MailWarningIcon className="size-3.5 text-danger-fg" />
          <span>
            feedback-loop: <strong>{complaintType}</strong>
          </span>
        </div>
      ) : null}
      {diagnostic ? (
        <div className="font-mono text-2xs">
          <span className="text-muted-foreground">diagnostic:</span> <span>{diagnostic}</span>
        </div>
      ) : null}
      <details>
        <summary className="text-muted-foreground cursor-pointer text-2xs uppercase">
          Raw metadata
        </summary>
        <pre className="bg-background/60 mt-1 max-h-48 overflow-auto rounded-md border p-2 font-mono text-2xs break-all whitespace-pre-wrap">
          {JSON.stringify(metadata, null, 2)}
        </pre>
      </details>
    </div>
  );
}

// ── Template management (#635, #628) ───────────────────────────────

interface TemplatesResp {
  astroliftEmailTemplates: AstroliftEmailTemplate[];
}

interface TemplateStatsResp {
  astroliftEmailTemplateStats: AstroliftTemplateSendStatPoint[];
}

interface TemplateMutationResp {
  createEmailTemplate?: MutationResult<AstroliftEmailTemplate>;
  updateEmailTemplate?: MutationResult<AstroliftEmailTemplate>;
}

interface DeleteTemplateResp {
  deleteEmailTemplate: MutationResult<{ id: string; deleted: boolean }>;
}

function TemplateManagementPanel({ managedServiceId }: { managedServiceId: string }) {
  const queryVars = React.useMemo(() => ({ managedServiceId }), [managedServiceId]);
  const { data, loading } = useQuery<TemplatesResp>(GET_EMAIL_TEMPLATES, {
    variables: queryVars,
    fetchPolicy: "cache-and-network",
  });
  const refetchQueries = React.useMemo(
    () => [{ query: GET_EMAIL_TEMPLATES, variables: queryVars }],
    [queryVars]
  );

  const [createTemplate, { loading: creating }] = useMutation<TemplateMutationResp>(
    CREATE_EMAIL_TEMPLATE,
    {
      refetchQueries,
      awaitRefetchQueries: true,
    }
  );
  const [updateTemplate, { loading: updating }] = useMutation<TemplateMutationResp>(
    UPDATE_EMAIL_TEMPLATE,
    {
      refetchQueries,
      awaitRefetchQueries: true,
    }
  );
  const [deleteTemplate, { loading: deleting }] = useMutation<DeleteTemplateResp>(
    DELETE_EMAIL_TEMPLATE,
    {
      refetchQueries,
      awaitRefetchQueries: true,
    }
  );

  const [creatingNew, setCreatingNew] = React.useState(false);
  const [editingName, setEditingName] = React.useState<string | null>(null);
  const [statsOpen, setStatsOpen] = React.useState<Set<string>>(() => new Set<string>());
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftEmailTemplate | null>(null);

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
    const name = draft.name.trim();
    const subject = draft.subject.trim();
    if (!name) {
      toast.error("Template name required");
      return;
    }
    if (!subject) {
      toast.error("Subject required");
      return;
    }
    try {
      if (creatingNew) {
        const { data } = await createTemplate({
          variables: {
            input: {
              managedServiceId,
              name,
              subject,
              htmlBody: draft.htmlBody,
              textBody: draft.textBody,
            },
          },
        });
        const env = data?.createEmailTemplate;
        if (!env) {
          toast.error("No response from server");
          return;
        }
        if (!env.ok) {
          toast.error(env.errors[0]?.message ?? "Create template failed");
          return;
        }
        toast.success(`Created template ${name}`);
      } else if (editingName) {
        const { data } = await updateTemplate({
          variables: {
            input: {
              managedServiceId,
              name: editingName,
              subject,
              htmlBody: draft.htmlBody,
              textBody: draft.textBody,
            },
          },
        });
        const env = data?.updateEmailTemplate;
        if (!env) {
          toast.error("No response from server");
          return;
        }
        if (!env.ok) {
          toast.error(env.errors[0]?.message ?? "Update template failed");
          return;
        }
        toast.success(`Updated template ${editingName}`);
      }
      cancelForm();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      const { data } = await deleteTemplate({
        variables: {
          input: { managedServiceId, name: deleteTarget.name },
        },
      });
      const env = data?.deleteEmailTemplate;
      if (!env) return;
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? "Delete failed");
        return;
      }
      toast.success(`Deleted ${deleteTarget.name}`);
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  }

  const templates = data?.astroliftEmailTemplates ?? [];
  const formOpen = creatingNew || editingName !== null;
  const busy = creating || updating;

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
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Subject</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="w-28 text-right" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {templates.map((t) => {
              const isStatsOpen = statsOpen.has(t.name);
              return (
                <React.Fragment key={t.name}>
                  <TableRow>
                    <TableCell className="font-mono text-xs">{t.name}</TableCell>
                    <TableCell className="max-w-[14rem] truncate text-xs" title={t.subject}>
                      {t.subject}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-2xs">
                      {t.createdAt ? new Date(t.createdAt).toLocaleDateString() : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="inline-flex items-center gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-7"
                          onClick={() => toggleStats(t.name)}
                          title="Toggle 14d send stats"
                        >
                          <BarChart3Icon className="size-3.5" />
                          <span className="sr-only">Stats</span>
                        </Button>
                        <Can permission="managed_service.update">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-7"
                            onClick={() => beginEdit(t)}
                          >
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
                    </TableCell>
                  </TableRow>
                  {isStatsOpen ? (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={4} className="bg-muted/30 p-3">
                        <TemplateStats managedServiceId={managedServiceId} name={t.name} />
                      </TableCell>
                    </TableRow>
                  ) : null}
                </React.Fragment>
              );
            })}
          </TableBody>
        </Table>
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

function TemplateStats({ managedServiceId, name }: { managedServiceId: string; name: string }) {
  const { data, loading } = useQuery<TemplateStatsResp>(GET_EMAIL_TEMPLATE_STATS, {
    variables: { managedServiceId, name, days: 14 },
    fetchPolicy: "cache-and-network",
  });
  const points = data?.astroliftEmailTemplateStats ?? [];

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
                      className="absolute right-0 bottom-0 left-0 rounded-sm bg-danger/70"
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
