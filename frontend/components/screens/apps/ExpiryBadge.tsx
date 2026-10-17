"use client";

import { HourglassIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export type ExpiryTone = "ok" | "soon" | "imminent" | "expired";

/** How close a temporary app is to its teardown (#2433): the 24 h and 1 h warnings. */
export function expiryTone(remainingMs: number): ExpiryTone {
  if (remainingMs <= 0) return "expired";
  if (remainingMs <= HOUR) return "imminent";
  if (remainingMs <= DAY) return "soon";
  return "ok";
}

/** The two largest units left, rounded down: days and hours, hours and minutes, or minutes. */
export function remainingParts(remainingMs: number): {
  key: "days" | "hours" | "minutes";
  values: Record<string, number>;
} {
  const ms = Math.max(0, remainingMs);
  const days = Math.floor(ms / DAY);
  const hours = Math.floor((ms % DAY) / HOUR);
  const minutes = Math.max(1, Math.floor((ms % HOUR) / MINUTE));
  if (days > 0) return { key: "days", values: { days, hours } };
  if (hours > 0) return { key: "hours", values: { hours, minutes } };
  return { key: "minutes", values: { minutes } };
}

const TONE: Record<ExpiryTone, string> = {
  ok: "border-border text-muted-foreground",
  soon: "border-warning-border bg-warning/10 text-warning-fg",
  imminent: "border-destructive/40 bg-destructive/10 text-destructive",
  expired: "border-border bg-muted text-muted-foreground",
};

export interface ExpiryBadgeProps {
  /** The app's `expiresAt`; nothing renders for a permanent app. */
  expiresAt: string | null | undefined;
  /** Extend by one day. Absent without `app.update`, or once expired. */
  onExtend?: () => unknown;
  extending?: boolean;
  /** Fixed clock for stories and tests; otherwise the countdown ticks each minute. */
  now?: number;
  className?: string;
}

/**
 * A temporary app's countdown (#2433): "Expires in 3d 4h", amber inside the
 * last day, red inside the last hour, with a one-click "+1 day" when the
 * viewer may extend it.
 */
export function ExpiryBadge({
  expiresAt,
  onExtend,
  extending = false,
  now,
  className,
}: ExpiryBadgeProps) {
  const t = useTranslations("apps.expiry");
  const format = useFormatter();
  const [tick, setTick] = React.useState(() => now ?? Date.now());
  React.useEffect(() => {
    if (now !== undefined || !expiresAt) return;
    const id = window.setInterval(() => setTick(Date.now()), MINUTE);
    return () => window.clearInterval(id);
  }, [now, expiresAt]);
  if (!expiresAt) return null;

  const at = new Date(expiresAt);
  const remaining = at.getTime() - (now ?? tick);
  const tone = expiryTone(remaining);
  const parts = remainingParts(remaining);
  const label =
    tone === "expired"
      ? t("expired")
      : t("expiresIn", { remaining: t(`remaining.${parts.key}`, parts.values) });
  const when = format.dateTime(at, { dateStyle: "medium", timeStyle: "short" });

  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1", className)}>
      <span
        className={cn(
          "inline-flex h-5 items-center gap-1 rounded-4xl border px-2 text-xs font-medium whitespace-nowrap",
          TONE[tone]
        )}
        title={t("title", { when })}
        data-tone={tone}
      >
        <HourglassIcon className="size-3" aria-hidden />
        {label}
      </span>
      {onExtend && tone !== "expired" && (
        <Button
          size="sm"
          variant="ghost"
          className="h-5 px-1.5 text-xs"
          onClick={() => void onExtend()}
          disabled={extending}
          aria-label={t("extendLabel")}
        >
          {t("extend")}
        </Button>
      )}
    </span>
  );
}
