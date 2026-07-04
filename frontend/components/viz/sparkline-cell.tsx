import * as React from "react";

import { cn } from "@/lib/utils";

import { Sparkline } from "./sparkline";

export interface SparklineCellProps {
  data: number[];
  /**
   * Trailing value shown after the line. Defaults to the last data point;
   * pass a formatted node (e.g. "1.2k") to override, or `null` to hide.
   */
  value?: React.ReactNode | null;
  width?: number;
  height?: number;
  className?: string;
  ariaLabel?: string;
}

/**
 * A `@tanstack/react-table` cell renderer: a right-aligned sparkline with the
 * latest value beside it. Drop straight into a column `cell`:
 *
 * @example
 * { id: "trend", cell: ({ row }) => <SparklineCell data={row.original.series} /> }
 */
export function SparklineCell({
  data,
  value,
  width = 64,
  height = 20,
  className,
  ariaLabel,
}: SparklineCellProps) {
  const last = data.length ? data[data.length - 1] : null;
  const trailing = value === undefined ? last : value;

  return (
    <div className={cn("flex items-center justify-end gap-2", className)}>
      <Sparkline data={data} width={width} height={height} ariaLabel={ariaLabel} />
      {trailing !== null && (
        <span className="text-muted-foreground w-10 text-right text-xs tabular-nums">
          {trailing}
        </span>
      )}
    </div>
  );
}
