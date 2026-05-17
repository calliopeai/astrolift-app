"use client";

import { Cron } from "croner";
import cronstrue from "cronstrue";
import { useTranslations } from "next-intl";
import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * #427 — human-readable cron schedule + live "fires in N min"
 * countdown. The expression is rendered as the dim monospace anchor
 * (so operators can still copy the raw cron) and the human form +
 * countdown surface underneath.
 *
 * The countdown ticks every 60s — anything finer is wasted work
 * (cron firings are minute-granular). ``Cron`` is constructed once
 * per expression change and re-queried on every tick so we don't
 * drift past a firing without noticing.
 */

export interface CronSchedulePreviewProps {
  schedule: string;
  className?: string;
}

export function CronSchedulePreview({ schedule, className }: CronSchedulePreviewProps) {
  const t = useTranslations("jobs.cron");
  const human = useHumanSchedule(schedule, t("invalid"));
  const nextFire = useNextFire(schedule);

  return (
    <div className={cn("flex flex-col gap-0.5 text-xs", className)}>
      <code className="text-muted-foreground font-mono">{schedule}</code>
      <span className="text-foreground">{human}</span>
      {nextFire ? (
        <span className="text-muted-foreground" title={nextFire.toLocaleString()}>
          {t("firesIn", { relative: relativeFromNow(nextFire, t) })}
        </span>
      ) : null}
    </div>
  );
}

function useHumanSchedule(schedule: string, fallback: string): string {
  return React.useMemo(() => {
    try {
      return cronstrue.toString(schedule, { use24HourTimeFormat: true });
    } catch {
      return fallback;
    }
  }, [schedule, fallback]);
}

function useNextFire(schedule: string): Date | null {
  // Re-tick every 60s so the countdown stays accurate without a
  // requestAnimationFrame loop. Cron fires on minute boundaries so
  // sub-minute precision would be noise.
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  return React.useMemo(() => {
    try {
      const job = new Cron(schedule);
      // ``msToNext`` returns null when there's no next firing
      // (impossible for standard cron, possible for one-shot @yearly
      // patterns that already fired this year — defensive).
      const next = job.nextRun();
      return next;
    } catch {
      return null;
    }
    // Intentionally include ``tick`` so the memo refreshes each tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schedule, tick]);
}

function relativeFromNow(target: Date, t: ReturnType<typeof useTranslations<"jobs.cron">>): string {
  const deltaMs = target.getTime() - Date.now();
  if (deltaMs <= 0) return t("now");
  const minutes = Math.round(deltaMs / 60_000);
  if (minutes < 60) return t("minutes", { count: minutes });
  const hours = Math.round(minutes / 60);
  if (hours < 24) return t("hours", { count: hours });
  const days = Math.round(hours / 24);
  return t("days", { count: days });
}
