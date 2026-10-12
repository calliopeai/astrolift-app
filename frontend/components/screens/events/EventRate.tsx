"use client";

import { useTranslations } from "next-intl";

import { QueryError } from "@/components/QueryError";

import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Sparkline } from "@/components/viz";

import { RATE_WINDOW_DAYS, type useEventRate } from "./use-events";

export type EventRateProps = ReturnType<typeof useEventRate>;

/** Event velocity over the last two weeks, including a successful empty window. */
export function EventRate({ days, total, loading, error, onRetry }: EventRateProps) {
  const t = useTranslations("eventDetail");
  if (error) return <QueryError title={t("rateError")} error={error} onRetry={onRetry} />;
  if (loading)
    return (
      <Card aria-busy="true">
        <CardContent className="p-3">
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-4 p-3">
        <div>
          <p className="text-muted-foreground text-2xs tracking-wide uppercase">
            {t("lastDays", { days: RATE_WINDOW_DAYS })}
          </p>
          <p className="text-xl font-semibold tabular-nums">{total}</p>
          {total === 0 && <p className="text-muted-foreground text-xs">{t("noEventsInWindow")}</p>}
        </div>
        <Sparkline
          data={days}
          width={220}
          height={40}
          variant="area"
          className="text-chart-1"
          ariaLabel={t("rateAria")}
        />
      </CardContent>
    </Card>
  );
}
