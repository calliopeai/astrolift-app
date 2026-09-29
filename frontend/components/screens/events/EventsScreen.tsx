"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * The /events page chrome: the rate card, the search box and grouping
 * toggle, then whichever feed the toggle picked. The rate and the feed
 * arrive as slots so only the mounted feed runs its query. Both feeds take
 * the same search term, so toggling grouping keeps what the operator typed.
 */
export function EventsScreen({
  rate,
  search,
  onSearchChange,
  aggregate,
  onAggregateChange,
  children,
}: {
  rate: React.ReactNode;
  /** The box's text as typed; the caller debounces it into the query. */
  search: string;
  onSearchChange: (next: string) => void;
  aggregate: boolean;
  onAggregateChange: (next: boolean) => void;
  children: React.ReactNode;
}) {
  const t = useTranslations("lists.events");
  return (
    <PageShell title={t("title")} description={t("description")}>
      {rate}
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <Input
          type="search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={t("filterPlaceholder")}
          aria-label={t("filterPlaceholder")}
          className="max-w-md min-w-0 flex-1"
        />
        <EventsAggregateToggle checked={aggregate} onCheckedChange={onAggregateChange} />
      </div>
      {children}
    </PageShell>
  );
}

/**
 * Beside the search box. The box is server-side, debounced, and matches on
 * event type, resource kind/id and app slug.
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
