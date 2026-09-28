"use client";

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

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { DataTable, type Column } from "@/components/data-table";
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
import { useFormatters } from "@/lib/i18n/formatters";

import { formatRemaining } from "./alert-format";
import { CreateAlertRuleSheet } from "./CreateAlertRuleSheet";
import { MuteAlertRuleSheet } from "./MuteAlertRuleSheet";
import type { AlertEvent, AlertRule, useAlerts } from "./use-alerts";

/**
 * Cells that carry their own controls have to sit above `rowHref`'s
 * stretched row link, which is an overlay across the whole row.
 */
const ABOVE_ROW_LINK = "relative z-10";

const SEVERITY_TONE: Record<string, "ok" | "warn" | "error" | "muted"> = {
  info: "ok",
  warn: "warn",
  warning: "warn",
  critical: "error",
  error: "error",
};

export type AlertsScreenProps = ReturnType<typeof useAlerts>;

/** Alerts: the rule and event tables, stat cards, and rule create / mute / delete. */
export function AlertsScreen({
  rulesTable,
  eventsTable,
  ruleCount,
  activeRuleCount,
  unresolvedCount,
  eventCount,
  busy,
  createRule,
  deleteRule,
  acknowledge,
  mutePreset,
  muteCustom,
  unmute,
}: AlertsScreenProps) {
  const t = useTranslations("lists.alerts");
  const fmt = useFormatters();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<AlertRule | null>(null);
  const [muteTarget, setMuteTarget] = React.useState<AlertRule | null>(null);

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
                      void unmute(r);
                    }}
                  >
                    <Volume2Icon className="size-4" />
                    {t("mute.unmute")}
                  </DropdownMenuItem>
                ) : (
                  <>
                    <DropdownMenuItem
                      onSelect={() => {
                        void mutePreset(r, 1, "1h");
                      }}
                    >
                      <VolumeOffIcon className="size-4" />
                      {t("mute.preset1h")}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => {
                        void mutePreset(r, 4, "4h");
                      }}
                    >
                      <VolumeOffIcon className="size-4" />
                      {t("mute.preset4h")}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => {
                        void mutePreset(r, 24, "24h");
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
            <Button variant="ghost" size="sm" onClick={() => acknowledge(e)} disabled={busy}>
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
            <p className="text-muted-foreground text-xs tracking-wide uppercase">
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
            <p className="text-muted-foreground text-xs tracking-wide uppercase">
              {t("stats.unresolved")}
            </p>
            <p className="text-destructive mt-1 text-2xl font-bold">{unresolvedCount}</p>
            <p className="text-muted-foreground text-xs">{t("stats.inWindow")}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-muted-foreground text-xs tracking-wide uppercase">
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

      <CreateAlertRuleSheet
        open={createOpen}
        onOpenChange={setCreateOpen}
        onSubmit={async (input) => {
          const ok = await createRule(input);
          if (ok) setCreateOpen(false);
          return ok;
        }}
        busy={busy}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget ? t("delete.title", { name: deleteTarget.name }) : t("delete.fallbackTitle")
        }
        description={t("delete.description")}
        confirmLabel={t("delete.confirm")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await deleteRule(deleteTarget);
        }}
      />

      <MuteAlertRuleSheet
        key={muteTarget?.id ?? "none"}
        target={muteTarget}
        onOpenChange={(next) => {
          if (!next) setMuteTarget(null);
        }}
        onSubmit={async (durationSeconds, reason) => {
          if (muteTarget) {
            const ok = await muteCustom(muteTarget, durationSeconds, reason);
            if (ok) setMuteTarget(null);
          }
        }}
        busy={busy}
      />
    </PageShell>
  );
}
