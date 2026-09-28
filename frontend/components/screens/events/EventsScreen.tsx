"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Label } from "@/components/ui/label";

/**
 * The /events page chrome: the rate card, then whichever list the grouping
 * toggle picked. Both arrive as slots so only the mounted list runs its
 * query.
 */
export function EventsScreen({
  rate,
  children,
}: {
  rate: React.ReactNode;
  children: React.ReactNode;
}) {
  const t = useTranslations("lists.events");
  return (
    <PageShell title={t("title")} description={t("description")}>
      {rate}
      {/* Both lists read and write `?ev-q=`, so toggling grouping keeps the
          search term the operator typed. Only one is ever mounted. */}
      {children}
    </PageShell>
  );
}

/**
 * Rides in DataTable's toolbar, beside the search box. The box is the
 * controller's own: server-side, debounced, and matching on event type,
 * resource kind/id and app slug — it replaces both the per-keystroke
 * `eventType` input and the client-side filter that sat on top of it.
 */
export function EventsAggregateToggle({
  checked,
  onCheckedChange,
}: {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
}) {
  const t = useTranslations("lists.events");
  return (
    <Label className="text-muted-foreground flex items-center gap-2 text-xs">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onCheckedChange(e.target.checked)}
        aria-label={t("aggregateToggle")}
      />
      {t("aggregateToggle")}
    </Label>
  );
}
