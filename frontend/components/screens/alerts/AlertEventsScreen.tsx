"use client";

import { BellIcon, CheckIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Can } from "@/components/Can";
import { Feed } from "@/components/feed/Feed";
import { adminCrumbs } from "@/components/screens/administration/insights/header";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useFormatters } from "@/lib/i18n/formatters";

import type { AlertEvent, useAlertEvents } from "./use-alerts";

const SEVERITY_TONE: Record<string, "ok" | "warn" | "error" | "muted"> = {
  info: "ok",
  warn: "warn",
  warning: "warn",
  critical: "error",
  error: "error",
};

export type AlertEventsScreenProps = ReturnType<typeof useAlertEvents>;

/**
 * Admin › Alerts › Events: the firing instances of every rule as a Feed
 * (list rule 5: events keep arriving), views All · Firing as the header's
 * tabs, newest first, grouped by day, older ones loading as the reader
 * nears the end of the frame. Each line opens the event; Ack silences one
 * while it is worked. Pure; the data half is useAlertEvents.
 */
export function AlertEventsScreen({ view, events, busy, acknowledge }: AlertEventsScreenProps) {
  const t = useTranslations("lists.alerts");
  const fmt = useFormatters();

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader
        crumbs={[
          ...adminCrumbs("alerts", t("title")).slice(0, 1),
          { label: t("title"), href: "/alerts" },
          { label: t("events.title") },
        ]}
        title={t("events.title")}
        context={t("events.description")}
        tabs={[
          { key: "all", label: "All", href: "/alerts/events", active: view === "all" },
          {
            key: "firing",
            label: "Firing",
            href: "/alerts/events?view=firing",
            active: view === "firing",
          },
        ]}
        tabsAriaLabel="Views"
      />
      <Feed<AlertEvent>
        label="Alert events"
        {...events}
        keyOf={(e) => e.id}
        groupBy={{ day: (e) => e.firedAt }}
        maxHeight="max-h-160"
        empty={{
          icon: <BellIcon className="size-5" />,
          title: t("events.emptyTitle"),
          description: t("events.emptyDescription"),
        }}
        renderItem={(e) => (
          <div className="flex min-w-0 items-start gap-3 px-1">
            <StatusDot status={SEVERITY_TONE[e.severity] ?? "muted"} className="mt-1.5 shrink-0" />
            <div className="min-w-0 flex-1">
              <Link
                href={`/alerts/events/${e.id}`}
                className="block min-w-0 text-sm font-medium [overflow-wrap:anywhere] hover:underline"
              >
                {e.summary}
              </Link>
              <div className="text-muted-foreground mt-0.5 flex min-w-0 flex-wrap items-center gap-2 text-xs">
                <Badge variant="secondary" className="capitalize">
                  {e.severity}
                </Badge>
                {e.resolvedAt ? (
                  <Badge variant="outline">{t("events.resolved")}</Badge>
                ) : e.acknowledgedAt ? (
                  <Badge variant="secondary">{t("events.acknowledged")}</Badge>
                ) : (
                  <Badge variant="destructive">{t("events.firing")}</Badge>
                )}
                <time dateTime={e.firedAt} className="font-mono">
                  {fmt.formatDateTime(e.firedAt)}
                </time>
              </div>
            </div>
            {!e.resolvedAt && !e.acknowledgedAt && (
              <Can permission="org.update">
                <Button variant="ghost" size="sm" onClick={() => acknowledge(e)} disabled={busy}>
                  <CheckIcon className="size-3.5" />
                  {t("events.ack")}
                </Button>
              </Can>
            )}
          </div>
        )}
      />
    </div>
  );
}
