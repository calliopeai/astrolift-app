"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Sparkline } from "@/components/viz";

import { RATE_WINDOW_DAYS, type useEventRate } from "./use-events";

export type EventRateProps = ReturnType<typeof useEventRate>;

/** Event velocity over the last two weeks; hidden when the window is empty. */
export function EventRate({ days, total }: EventRateProps) {
  if (total === 0) return null;
  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-4 p-3">
        <div>
          <p className="text-muted-foreground text-2xs tracking-wide uppercase">
            Last {RATE_WINDOW_DAYS} days
          </p>
          <p className="text-xl font-semibold tabular-nums">{total}</p>
        </div>
        <Sparkline
          data={days}
          width={220}
          height={40}
          variant="area"
          className="text-chart-1"
          ariaLabel="Event rate over the last two weeks"
        />
      </CardContent>
    </Card>
  );
}
