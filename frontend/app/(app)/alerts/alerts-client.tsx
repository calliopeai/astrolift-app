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
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
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
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  ACKNOWLEDGE_ALERT_EVENT,
  CREATE_ALERT_RULE,
  DELETE_ALERT_RULE,
  LIST_ALERT_EVENTS,
  LIST_ALERT_RULES,
  MUTE_ALERT_RULE,
  UNMUTE_ALERT_RULE,
} from "@/graphql/operations/alerts.queries";
import { useFormatters } from "@/lib/i18n/formatters";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { useListControls } from "@/hooks/use-list-controls";

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

interface RulesResp {
  astroliftAlertRules: AlertRule[];
}
interface EventsResp {
  astroliftAlertEvents: AlertEvent[];
}

const SEVERITY_TONE: Record<string, "ok" | "warn" | "error" | "muted"> = {
  info: "ok",
  warn: "warn",
  warning: "warn",
  critical: "error",
  error: "error",
};

const SEVERITY_ORDER: Record<string, number> = {
  info: 0,
  warn: 1,
  warning: 1,
  critical: 2,
  error: 2,
};

const TARGETS = ["app", "env", "workload", "global"];
const SEVERITIES = ["info", "warn", "critical"];

export function AlertsClient() {
  const t = useTranslations("lists.alerts");
  const fmt = useFormatters();
  const [createOpen, setCreateOpen] = React.useState(false);
  const rules = useQuery<RulesResp>(LIST_ALERT_RULES, {
    variables: { activeOnly: false },
    fetchPolicy: "cache-and-network",
  });
  const events = useQuery<EventsResp>(LIST_ALERT_EVENTS, {
    variables: { unresolvedOnly: false, limit: 100 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30000,
  });

  const refetch = [
    { query: LIST_ALERT_RULES, variables: { activeOnly: false } },
    {
      query: LIST_ALERT_EVENTS,
      variables: { unresolvedOnly: false, limit: 100 },
    },
  ];

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
  const ruleList = rules.data?.astroliftAlertRules ?? [];
  const eventList = events.data?.astroliftAlertEvents ?? [];
  const unresolvedEvents = eventList.filter((e) => !e.resolvedAt);

  const [deleteTarget, setDeleteTarget] = React.useState<AlertRule | null>(null);
  const [muteTarget, setMuteTarget] = React.useState<AlertRule | null>(null);
  const [showMuted, setShowMuted] = React.useState(false);

  const visibleRules = showMuted
    ? ruleList
    : ruleList.filter((r) => !r.activeMute);
  const mutedCount = ruleList.filter((r) => r.activeMute).length;

  const rulesCtrl = useListControls({
    data: visibleRules,
    searchFn: (r) => [r.name, r.target, r.severity].join(" "),
    initialPageSize: 25,
    sortFn: (a, b, sort) => {
      if (sort.key === "name") {
        const cmp = a.name.localeCompare(b.name);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      if (sort.key === "severity") {
        const cmp = (SEVERITY_ORDER[a.severity] ?? 0) - (SEVERITY_ORDER[b.severity] ?? 0);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      if (sort.key === "createdAt") {
        const cmp = a.createdAt.localeCompare(b.createdAt);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      return 0;
    },
  });

  const eventsCtrl = useListControls({
    data: eventList,
    searchFn: (e) => [e.summary, e.severity].join(" "),
    initialPageSize: 25,
    sortFn: (a, b, sort) => {
      if (sort.key === "severity") {
        const cmp = (SEVERITY_ORDER[a.severity] ?? 0) - (SEVERITY_ORDER[b.severity] ?? 0);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      if (sort.key === "firedAt") {
        const cmp = a.firedAt.localeCompare(b.firedAt);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      return 0;
    },
  });

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
            <p className="mt-1 text-2xl font-bold">{ruleList.length}</p>
            <p className="text-muted-foreground text-xs">
              {t("stats.active", { count: ruleList.filter((r) => r.isActive).length })}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-muted-foreground text-xs uppercase tracking-wide">
              {t("stats.unresolved")}
            </p>
            <p className="mt-1 text-2xl font-bold text-destructive">
              {unresolvedEvents.length}
            </p>
            <p className="text-muted-foreground text-xs">{t("stats.inWindow")}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-muted-foreground text-xs uppercase tracking-wide">
              {t("stats.total")}
            </p>
            <p className="mt-1 text-2xl font-bold">{eventList.length}</p>
            <p className="text-muted-foreground text-xs">{t("stats.latest")}</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="flex items-center justify-between border-b p-4">
            <div>
              <h2 className="font-medium">{t("rules.title")}</h2>
              <p className="text-muted-foreground text-xs">
                {t("rules.description")}
              </p>
            </div>
            {mutedCount > 0 && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowMuted((v) => !v)}
              >
                {showMuted ? t("mute.hideMuted") : t("mute.showMuted")}{" "}
                ({mutedCount})
              </Button>
            )}
          </div>
          {rules.loading && ruleList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : visibleRules.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BellIcon className="size-5" />}
                title={t("rules.emptyTitle")}
                description={t("rules.emptyDescription")}
              />
            </div>
          ) : (
            <>
              <div className="px-4 py-2 border-b">
                <ListControls controls={rulesCtrl} searchPlaceholder="Search rules…" />
              </div>
              {rulesCtrl.rows.length === 0 ? (
                <div className="p-6">
                  <EmptyState
                    icon={<BellIcon className="size-5" />}
                    title={t("rules.emptyTitle")}
                    description={t("rules.emptyDescription")}
                  />
                </div>
              ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>
                    <SortableHeader sortKey="name" sort={rulesCtrl.sort} onToggle={rulesCtrl.toggleSort}>
                      {t("rules.columns.name")}
                    </SortableHeader>
                  </TableHead>
                  <TableHead>{t("rules.columns.target")}</TableHead>
                  <TableHead>
                    <SortableHeader sortKey="severity" sort={rulesCtrl.sort} onToggle={rulesCtrl.toggleSort}>
                      {t("rules.columns.severity")}
                    </SortableHeader>
                  </TableHead>
                  <TableHead>
                    <SortableHeader sortKey="createdAt" sort={rulesCtrl.sort} onToggle={rulesCtrl.toggleSort}>
                      {t("rules.columns.created")}
                    </SortableHeader>
                  </TableHead>
                  <TableHead className="text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rulesCtrl.rows.map((r) => (
                  <TableRow key={r.id} className={r.activeMute ? "opacity-70" : undefined}>
                    <TableCell className="w-8">
                      {r.activeMute ? (
                        <VolumeOffIcon className="text-muted-foreground size-4" />
                      ) : r.isActive ? (
                        <BellIcon className="size-4 text-emerald-500" />
                      ) : (
                        <BellOffIcon className="text-muted-foreground size-4" />
                      )}
                    </TableCell>
                    <TableCell className="font-medium">
                      {r.name}
                      {r.activeMute && (
                        <Badge variant="secondary" className="ml-2 text-[11px]">
                          {t("mute.badge", {
                            remaining: formatRemaining(r.activeMute.ttlUntil),
                          })}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{r.target}</Badge>
                      {r.targetId && (
                        <span className="text-muted-foreground ml-2 font-mono text-[11px]">
                          {r.targetId}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {r.severity}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {fmt.formatDate(r.createdAt)}
                    </TableCell>
                    <TableCell className="text-right">
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
                                  <DropdownMenuItem
                                    onSelect={() => setMuteTarget(r)}
                                  >
                                    <VolumeOffIcon className="size-4" />
                                    {t("mute.presetCustom")}
                                  </DropdownMenuItem>
                                </>
                              )}
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                onSelect={() => setDeleteTarget(r)}
                                variant="destructive"
                              >
                                <Trash2Icon className="size-4" />
                                {t("delete.confirm")}
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </Can>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          <div className="border-b p-4">
            <h2 className="font-medium">{t("events.title")}</h2>
            <p className="text-muted-foreground text-xs">{t("events.description")}</p>
          </div>
          {events.loading && eventList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : eventList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BellIcon className="size-5" />}
                title={t("events.emptyTitle")}
                description={t("events.emptyDescription")}
              />
            </div>
          ) : (
            <>
              <div className="px-4 py-2 border-b">
                <ListControls controls={eventsCtrl} searchPlaceholder="Search events…" />
              </div>
              {eventsCtrl.rows.length === 0 ? (
                <div className="p-6">
                  <EmptyState
                    icon={<BellIcon className="size-5" />}
                    title={t("events.emptyTitle")}
                    description={t("events.emptyDescription")}
                  />
                </div>
              ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>{t("events.columns.summary")}</TableHead>
                  <TableHead>
                    <SortableHeader sortKey="severity" sort={eventsCtrl.sort} onToggle={eventsCtrl.toggleSort}>
                      {t("events.columns.severity")}
                    </SortableHeader>
                  </TableHead>
                  <TableHead>
                    <SortableHeader sortKey="firedAt" sort={eventsCtrl.sort} onToggle={eventsCtrl.toggleSort}>
                      {t("events.columns.fired")}
                    </SortableHeader>
                  </TableHead>
                  <TableHead>{t("events.columns.state")}</TableHead>
                  <TableHead className="text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {eventsCtrl.rows.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell className="w-8">
                      <StatusDot status={SEVERITY_TONE[e.severity] ?? "muted"} />
                    </TableCell>
                    <TableCell className="max-w-md truncate">
                      {e.summary}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {e.severity}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {fmt.formatDateTime(e.firedAt)}
                    </TableCell>
                    <TableCell>
                      {e.resolvedAt ? (
                        <Badge variant="outline">{t("events.resolved")}</Badge>
                      ) : e.acknowledgedAt ? (
                        <Badge variant="secondary">{t("events.acknowledged")}</Badge>
                      ) : (
                        <Badge variant="destructive">{t("events.firing")}</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {!e.resolvedAt && !e.acknowledgedAt && (
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
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
              )}
            </>
          )}
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
