"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  BellIcon,
  BellOffIcon,
  CheckIcon,
  MoreHorizontalIcon,
  PlusIcon,
  Trash2Icon,
  VolumeOffIcon,
  Volume2Icon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import {
  DataTable,
  useCursorTable,
  type Column,
  type CursorPage,
} from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { Textarea } from "@/components/ui/textarea";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  ACKNOWLEDGE_ALERT_EVENT,
  CREATE_ALERT_RULE,
  DELETE_ALERT_RULE,
  LIST_ALERT_EVENTS_PAGE,
  LIST_ALERT_RULES_PAGE,
  MUTE_ALERT_RULE,
  UNMUTE_ALERT_RULE,
} from "@/graphql/operations/alerts.queries";
import { useFormatters } from "@/lib/i18n/formatters";

/**
 * Cells that carry their own controls have to sit above `rowHref`'s
 * stretched row link, which is an overlay across the whole row.
 */
const ABOVE_ROW_LINK = "relative z-10";

interface AlertMute {
  id: string;
  ttlUntil: string;
  reason: string;
  createdBy: string;
}

interface AlertRule {
  id: string;
  name: string;
  target: string;
  targetId: string;
  severity: string;
  predicate: Record<string, unknown>;
  notifyChannels: Record<string, unknown>;
  isActive: boolean;
  organizationSlug: string;
  createdAt: string;
  updatedAt: string;
  activeMute: AlertMute | null;
}

interface AlertEvent {
  id: string;
  ruleId: string;
  severity: string;
  firedAt: string;
  resolvedAt?: string | null;
  acknowledgedAt?: string | null;
  summary: string;
  detail: Record<string, unknown>;
}

interface RulesPageResp {
  astroliftAlertRulesPage: CursorPage<AlertRule>;
}
interface EventsPageResp {
  astroliftAlertEventsPage: CursorPage<AlertEvent>;
}

const SEVERITY_TONE: Record<string, "ok" | "warn" | "error" | "muted"> = {
  info: "ok",
  warn: "warn",
  warning: "warn",
  critical: "error",
  error: "error",
};

const TARGETS = ["app", "env", "workload", "global"];
const SEVERITIES = ["info", "warn", "critical"];

export function AlertsClient() {
  const t = useTranslations("lists.alerts");
  const fmt = useFormatters();
  const [createOpen, setCreateOpen] = React.useState(false);

  // Both page fields take `search`; neither takes a sort argument, so no
  // column declares a `sortKey`. The comparators this file used to run
  // (name / severity / created) only ever reordered the rows already in
  // hand, which is the wrong order at every page boundary.
  const rulesTable = useCursorTable<AlertRule>({
    query: LIST_ALERT_RULES_PAGE,
    variables: { activeOnly: false },
    extract: (d) => (d as RulesPageResp | undefined)?.astroliftAlertRulesPage,
    searchVariable: "search",
    urlKey: "rule",
  });

  const eventsTable = useCursorTable<AlertEvent>({
    query: LIST_ALERT_EVENTS_PAGE,
    variables: { unresolvedOnly: false },
    extract: (d) => (d as EventsPageResp | undefined)?.astroliftAlertEventsPage,
    searchVariable: "search",
    urlKey: "event",
    pollInterval: 30000,
  });

  // Stat-card counts. `totalCount` is computed over the whole filtered
  // set, so `limit: 1` buys the number without the rows — the cards used
  // to count a capped array in the browser, which stopped being true at
  // the 201st rule and the 101st event.
  const activeRules = useQuery<RulesPageResp>(LIST_ALERT_RULES_PAGE, {
    variables: { activeOnly: true, limit: 1 },
    fetchPolicy: "cache-and-network",
  });
  const unresolved = useQuery<EventsPageResp>(LIST_ALERT_EVENTS_PAGE, {
    variables: { unresolvedOnly: true, limit: 1 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30000,
  });

  const ruleCount = rulesTable.totalCount ?? 0;
  const activeRuleCount = activeRules.data?.astroliftAlertRulesPage.totalCount ?? 0;
  const unresolvedCount = unresolved.data?.astroliftAlertEventsPage.totalCount ?? 0;
  const eventCount = eventsTable.totalCount ?? 0;

  // Refetch by operation name: every mutation below moves rows in both
  // walks *and* in the two count queries, which are the same documents at
  // different variables. A `{ query, variables }` entry would refresh one
  // variable set and leave the others stale.
  const refetch = ["ListAlertRulesPage", "ListAlertEventsPage"];

  const [createRule, createState] = useMutation<{
    createAlertRule: MutationResult<AlertRule>;
  }>(CREATE_ALERT_RULE, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [deleteRule, deleteState] = useMutation<{
    deleteAlertRule: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_ALERT_RULE, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [ackEvent, ackState] = useMutation<{
    acknowledgeAlertEvent: MutationResult<AlertEvent>;
  }>(ACKNOWLEDGE_ALERT_EVENT, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [muteRule, muteState] = useMutation<{
    muteAlertRule: MutationResult<AlertRule>;
  }>(MUTE_ALERT_RULE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [unmuteRule, unmuteState] = useMutation<{
    unmuteAlertRule: MutationResult<AlertRule>;
  }>(UNMUTE_ALERT_RULE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const busy =
    createState.loading ||
    deleteState.loading ||
    ackState.loading ||
    muteState.loading ||
    unmuteState.loading;

  const [deleteTarget, setDeleteTarget] = React.useState<AlertRule | null>(null);
  const [muteTarget, setMuteTarget] = React.useState<AlertRule | null>(null);

  async function handleDelete(r: AlertRule) {
    const { data } = await deleteRule({ variables: { input: { id: r.id } } });
    if (data?.deleteAlertRule.ok) {
      toast.success(`Deleted ${r.name}`);
    } else {
      throw new Error(data?.deleteAlertRule.errors?.[0]?.message ?? "Delete failed");
    }
  }

  async function handleAcknowledge(e: AlertEvent) {
    const { data } = await ackEvent({ variables: { input: { id: e.id } } });
    if (!data?.acknowledgeAlertEvent.ok) {
      toast.error(
        data?.acknowledgeAlertEvent.errors?.[0]?.message ?? "Ack failed",
      );
    }
  }

  async function handleMutePreset(
    r: AlertRule,
    durationHours: number,
    durationLabel: string,
  ) {
    const reason = `Quick mute (${durationLabel})`;
    const { data } = await muteRule({
      variables: {
        input: {
          ruleId: r.id,
          durationSeconds: Math.round(durationHours * 3600),
          reason,
        },
      },
    });
    if (data?.muteAlertRule.ok) {
      toast.success(
        t("mute.toastMuted", { name: r.name, duration: durationLabel }),
      );
    } else {
      toast.error(
        data?.muteAlertRule.errors?.[0]?.message ?? t("mute.toastMuteFailed"),
      );
    }
  }

  async function handleMuteCustom(
    r: AlertRule,
    durationSeconds: number,
    reason: string,
  ): Promise<boolean> {
    const { data } = await muteRule({
      variables: {
        input: { ruleId: r.id, durationSeconds, reason },
      },
    });
    if (data?.muteAlertRule.ok) {
      toast.success(
        t("mute.toastMuted", {
          name: r.name,
          duration: formatDurationSeconds(durationSeconds),
        }),
      );
      return true;
    }
    toast.error(
      data?.muteAlertRule.errors?.[0]?.message ?? t("mute.toastMuteFailed"),
    );
    return false;
  }

  async function handleUnmute(r: AlertRule) {
    const { data } = await unmuteRule({
      variables: { input: { ruleId: r.id } },
    });
    if (data?.unmuteAlertRule.ok) {
      toast.success(t("mute.toastUnmuted", { name: r.name }));
    } else {
      toast.error(
        data?.unmuteAlertRule.errors?.[0]?.message ??
          t("mute.toastUnmuteFailed"),
      );
    }
  }

  // The rule state icon rides in the name cell rather than in a column of
  // its own: `rowHref` stretches a link over the first cell, and an
  // icon-only first cell would leave that link with no accessible name.
  const ruleColumns: Column<AlertRule>[] = [
    {
      id: "name",
      header: t("rules.columns.name"),
      cell: (r) => (
        <span className="flex items-center gap-2 font-medium">
          {r.activeMute ? (
            <VolumeOffIcon className="text-muted-foreground size-4 shrink-0" />
          ) : r.isActive ? (
            <BellIcon className="text-success-fg size-4 shrink-0" />
          ) : (
            <BellOffIcon className="text-muted-foreground size-4 shrink-0" />
          )}
          {r.name}
          {r.activeMute && (
            <Badge variant="secondary" className="text-2xs">
              {t("mute.badge", { remaining: formatRemaining(r.activeMute.ttlUntil) })}
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "target",
      header: t("rules.columns.target"),
      cell: (r) => (
        <>
          <Badge variant="outline">{r.target}</Badge>
          {r.targetId && (
            <span className="text-muted-foreground text-2xs ml-2 font-mono">{r.targetId}</span>
          )}
        </>
      ),
    },
    {
      id: "severity",
      header: t("rules.columns.severity"),
      cell: (r) => (
        <Badge variant="secondary" className="capitalize">
          {r.severity}
        </Badge>
      ),
    },
    {
      id: "createdAt",
      header: t("rules.columns.created"),
      cell: (r) => (
        <span className="text-muted-foreground text-xs">{fmt.formatDate(r.createdAt)}</span>
      ),
    },
    {
      id: "actions",
      // The header stayed blank in the old table; sr-only keeps that look
      // without leaving the column unnamed for a screen reader.
      header: <span className="sr-only">Actions</span>,
      align: "right",
      width: "w-16",
      cellClassName: ABOVE_ROW_LINK,
      cell: (r) => (
        <div className="flex items-center justify-end gap-1">
          <Can permission="org.update">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  disabled={busy}
                  aria-label={t("mute.menuLabel")}
                >
                  <MoreHorizontalIcon className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {r.activeMute ? (
                  <DropdownMenuItem
                    onSelect={() => {
                      void handleUnmute(r);
                    }}
                  >
                    <Volume2Icon className="size-4" />
                    {t("mute.unmute")}
                  </DropdownMenuItem>
                ) : (
                  <>
                    <DropdownMenuItem
                      onSelect={() => {
                        void handleMutePreset(r, 1, "1h");
                      }}
                    >
                      <VolumeOffIcon className="size-4" />
                      {t("mute.preset1h")}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => {
                        void handleMutePreset(r, 4, "4h");
                      }}
                    >
                      <VolumeOffIcon className="size-4" />
                      {t("mute.preset4h")}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => {
                        void handleMutePreset(r, 24, "24h");
                      }}
                    >
                      <VolumeOffIcon className="size-4" />
                      {t("mute.preset24h")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => setMuteTarget(r)}>
                      <VolumeOffIcon className="size-4" />
                      {t("mute.presetCustom")}
                    </DropdownMenuItem>
                  </>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => setDeleteTarget(r)} variant="destructive">
                  <Trash2Icon className="size-4" />
                  {t("delete.confirm")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </Can>
        </div>
      ),
    },
  ];

  const eventColumns: Column<AlertEvent>[] = [
    {
      id: "summary",
      header: t("events.columns.summary"),
      cell: (e) => (
        <span className="flex items-center gap-2">
          <StatusDot status={SEVERITY_TONE[e.severity] ?? "muted"} />
          <span className="max-w-md truncate">{e.summary}</span>
        </span>
      ),
    },
    {
      id: "severity",
      header: t("events.columns.severity"),
      cell: (e) => (
        <Badge variant="secondary" className="capitalize">
          {e.severity}
        </Badge>
      ),
    },
    {
      id: "firedAt",
      header: t("events.columns.fired"),
      cell: (e) => (
        <span className="text-muted-foreground text-xs">{fmt.formatDateTime(e.firedAt)}</span>
      ),
    },
    {
      id: "state",
      header: t("events.columns.state"),
      cell: (e) =>
        e.resolvedAt ? (
          <Badge variant="outline">{t("events.resolved")}</Badge>
        ) : e.acknowledgedAt ? (
          <Badge variant="secondary">{t("events.acknowledged")}</Badge>
        ) : (
          <Badge variant="destructive">{t("events.firing")}</Badge>
        ),
    },
    {
      id: "actions",
      header: <span className="sr-only">{t("events.ack")}</span>,
      align: "right",
      width: "w-24",
      cellClassName: ABOVE_ROW_LINK,
      cell: (e) =>
        !e.resolvedAt && !e.acknowledgedAt ? (
          <Can permission="org.update">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleAcknowledge(e)}
              disabled={busy}
            >
              <CheckIcon className="size-3.5" />
              {t("events.ack")}
            </Button>
          </Can>
        ) : null,
    },
  ];

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
      actions={
        <Can permission="org.update">
          <Button onClick={() => setCreateOpen(true)}>
            <PlusIcon className="size-4" />
            {t("newRule")}
          </Button>
        </Can>
      }
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardContent className="p-4">
            <p className="text-muted-foreground text-xs uppercase tracking-wide">
              {t("stats.rules")}
            </p>
            <p className="mt-1 text-2xl font-bold">{ruleCount}</p>
            <p className="text-muted-foreground text-xs">
              {t("stats.active", { count: activeRuleCount })}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-muted-foreground text-xs uppercase tracking-wide">
              {t("stats.unresolved")}
            </p>
            <p className="mt-1 text-2xl font-bold text-destructive">{unresolvedCount}</p>
            <p className="text-muted-foreground text-xs">{t("stats.inWindow")}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-muted-foreground text-xs uppercase tracking-wide">
              {t("stats.total")}
            </p>
            <p className="mt-1 text-2xl font-bold">{eventCount}</p>
            {/* `stats.latest` ("latest 100") described the capped fetch this
                card used to count. The number above it is the server's total
                now, so the caption is dropped rather than left saying
                something false. */}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="flex flex-col gap-3 p-4">
          <div>
            <h2 className="font-medium">{t("rules.title")}</h2>
            <p className="text-muted-foreground text-xs">{t("rules.description")}</p>
          </div>
          <DataTable
            label="Alert rules"
            controller={rulesTable}
            columns={ruleColumns}
            getRowId={(r) => r.id}
            rowHref={(r) => `/alerts/rules/${r.id}`}
            rowClassName={(r) => (r.activeMute ? "opacity-70" : undefined)}
            searchPlaceholder="Search rules…"
            empty={{
              icon: <BellIcon className="size-5" />,
              title: t("rules.emptyTitle"),
              description: t("rules.emptyDescription"),
            }}
            // Hardcoded rather than translated: adding a key here means
            // editing all eight locale files, which is a separate change.
            emptyFiltered={{
              title: "No matching rules",
              description:
                "No rule matches that search. The server matches the rule name and the target it covers (app slug, env name, workload slug).",
            }}
          />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-col gap-3 p-4">
          <div>
            <h2 className="font-medium">{t("events.title")}</h2>
            <p className="text-muted-foreground text-xs">{t("events.description")}</p>
          </div>
          <DataTable
            label="Alert events"
            controller={eventsTable}
            columns={eventColumns}
            getRowId={(e) => e.id}
            rowHref={(e) => `/alerts/events/${e.id}`}
            searchPlaceholder="Search events…"
            empty={{
              icon: <BellIcon className="size-5" />,
              title: t("events.emptyTitle"),
              description: t("events.emptyDescription"),
            }}
            emptyFiltered={{
              title: "No matching events",
              description:
                "No firing event matches that search. The server matches the event summary and the name of the rule that fired it.",
            }}
          />
        </CardContent>
      </Card>

      <CreateRuleSheet
        open={createOpen}
        onOpenChange={setCreateOpen}
        onSubmit={async (input) => {
          const { data } = await createRule({ variables: { input } });
          if (data?.createAlertRule.ok) {
            toast.success(`Created ${input.name}`);
            setCreateOpen(false);
            return true;
          }
          toast.error(
            data?.createAlertRule.errors?.[0]?.message ?? "Create failed",
          );
          return false;
        }}
        busy={busy}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget
            ? t("delete.title", { name: deleteTarget.name })
            : t("delete.fallbackTitle")
        }
        description={t("delete.description")}
        confirmLabel={t("delete.confirm")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />

      <MuteSheet
        key={muteTarget?.id ?? "none"}
        target={muteTarget}
        onOpenChange={(next) => {
          if (!next) setMuteTarget(null);
        }}
        onSubmit={async (durationSeconds, reason) => {
          if (muteTarget) {
            const ok = await handleMuteCustom(muteTarget, durationSeconds, reason);
            if (ok) setMuteTarget(null);
          }
        }}
        busy={busy}
      />
    </PageShell>
  );
}

function MuteSheet({
  target,
  onOpenChange,
  onSubmit,
  busy,
}: {
  target: AlertRule | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (durationSeconds: number, reason: string) => Promise<void>;
  busy: boolean;
}) {
  const t = useTranslations("lists.alerts.mute");
  const [hours, setHours] = React.useState("2");
  const [reason, setReason] = React.useState("");

  return (
    <Sheet open={target !== null} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-md">
        <SheetHeader>
          <SheetTitle>
            {target ? t("customTitle", { name: target.name }) : t("customTitle", { name: "" })}
          </SheetTitle>
          <SheetDescription>{t("customDescription")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            const h = Number(hours);
            if (!Number.isFinite(h) || h <= 0) return;
            if (!reason.trim()) return;
            await onSubmit(Math.round(h * 3600), reason.trim());
          }}
          className="flex flex-1 flex-col gap-4 overflow-auto px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="mute-hours">{t("durationLabel")}</Label>
            <Input
              id="mute-hours"
              type="number"
              min={1}
              max={168}
              step={1}
              value={hours}
              onChange={(e) => setHours(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="mute-reason">{t("reasonLabel")}</Label>
            <Textarea
              id="mute-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("reasonPlaceholder")}
              rows={4}
              required
            />
            <p className="text-muted-foreground text-xs">{t("reasonHint")}</p>
          </div>
          <SheetFooter className="flex-row justify-end gap-2 px-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !reason.trim()}>
              {t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function formatRemaining(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "0m";
  return formatDurationSeconds(Math.round(ms / 1000));
}

function formatDurationSeconds(total: number): string {
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  if (hours < 24) {
    return remMinutes > 0 ? `${hours}h ${remMinutes}m` : `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours > 0 ? `${days}d ${remHours}h` : `${days}d`;
}

function CreateRuleSheet({
  open,
  onOpenChange,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: {
    name: string;
    target: string;
    targetId: string | null;
    severity: string;
    predicate: Record<string, unknown> | null;
    notifyChannels: Record<string, unknown> | null;
  }) => Promise<boolean>;
  busy: boolean;
}) {
  const t = useTranslations("lists.alerts.create");
  const [name, setName] = React.useState("");
  const [target, setTarget] = React.useState("global");
  const [targetId, setTargetId] = React.useState("");
  const [severity, setSeverity] = React.useState("warn");
  const [predicateText, setPredicateText] = React.useState(
    JSON.stringify({ event: "deployment.failed" }, null, 2),
  );
  const [channelsText, setChannelsText] = React.useState(
    JSON.stringify({ slack: "#oncall" }, null, 2),
  );
  const [predicateError, setPredicateError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setName("");
      setTarget("global");
      setTargetId("");
      setSeverity("warn");
      setPredicateText(JSON.stringify({ event: "deployment.failed" }, null, 2));
      setChannelsText(JSON.stringify({ slack: "#oncall" }, null, 2));
      setPredicateError(null);
    }
  }, [open]);

  const isGlobal = target === "global";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!name.trim()) return;
            let predicate: Record<string, unknown> | null = null;
            let channels: Record<string, unknown> | null = null;
            try {
              predicate = predicateText.trim() ? JSON.parse(predicateText) : null;
              channels = channelsText.trim() ? JSON.parse(channelsText) : null;
              setPredicateError(null);
            } catch (err) {
              setPredicateError(String(err));
              return;
            }
            await onSubmit({
              name: name.trim(),
              target,
              targetId: isGlobal ? null : targetId.trim() || null,
              severity,
              predicate,
              notifyChannels: channels,
            });
          }}
          className="flex flex-1 flex-col gap-4 overflow-auto px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="ar-name">{t("nameLabel")}</Label>
            <Input
              id="ar-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("namePlaceholder")}
              autoFocus
              required
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="ar-target">{t("targetLabel")}</Label>
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger id="ar-target">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TARGETS.map((tgt) => (
                    <SelectItem key={tgt} value={tgt}>
                      {tgt}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="ar-severity">{t("severityLabel")}</Label>
              <Select value={severity} onValueChange={setSeverity}>
                <SelectTrigger id="ar-severity">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEVERITIES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-target-id">{t("targetIdLabel")}</Label>
            <Input
              id="ar-target-id"
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              placeholder={isGlobal ? t("targetIdGlobal") : t("targetIdPlaceholder")}
              disabled={isGlobal}
              spellCheck={false}
              className="font-mono"
            />
            <p className="text-muted-foreground text-xs">
              {isGlobal ? t("targetIdGlobalHint") : t("targetIdHint")}
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-predicate">{t("predicateLabel")}</Label>
            <Textarea
              id="ar-predicate"
              value={predicateText}
              onChange={(e) => setPredicateText(e.target.value)}
              rows={6}
              spellCheck={false}
              className="font-mono text-xs"
            />
            {predicateError && (
              <p className="text-destructive text-xs">{predicateError}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-channels">{t("channelsLabel")}</Label>
            <Textarea
              id="ar-channels"
              value={channelsText}
              onChange={(e) => setChannelsText(e.target.value)}
              rows={4}
              spellCheck={false}
              className="font-mono text-xs"
            />
          </div>
          <SheetFooter className="flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
