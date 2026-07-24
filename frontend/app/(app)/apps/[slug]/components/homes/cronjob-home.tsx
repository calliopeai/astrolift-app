"use client";

import cronstrue from "cronstrue";
import { HistoryIcon, RepeatIcon, ScrollTextIcon, TimerIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { nextCronRun } from "@/lib/cron";

interface CronjobHomeProps {
  slug: string;
  name: string;
  workload: AstroliftWorkload;
}

function describe(schedule: string): string {
  try {
    return cronstrue.toString(schedule, { verbose: true, use24HourTimeFormat: false });
  } catch {
    return "Custom schedule";
  }
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "due now";
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

/**
 * Primitive home for a **cronjob** — schedule-first ("watch"): the cron
 * expression front and centre, a plain-English reading, and a live countdown to
 * the next fire time. Run history slots below (graceful empty state until the
 * per-run feed is wired).
 */
export function CronjobHome({ slug, name, workload }: CronjobHomeProps) {
  const schedule = workload.schedule || "";
  const [now, setNow] = React.useState<number | null>(null);
  React.useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const next = React.useMemo(
    () => (schedule && now != null ? nextCronRun(schedule, new Date(now)) : null),
    [schedule, now]
  );

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <TimerIcon className="text-muted-foreground size-5" />
          </span>
          <span>{name}</span>
          <Badge variant="outline" className="gap-1.5">
            <TimerIcon className="size-3" />
            Scheduled job
          </Badge>
        </span>
      }
      description={
        <span className="text-muted-foreground text-xs">
          {schedule ? describe(schedule) : "No schedule set"}
        </span>
      }
    >
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <RepeatIcon className="size-4" />
              Schedule
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-6 sm:grid-cols-2">
            <div className="space-y-1">
              <div className="text-muted-foreground text-xs uppercase tracking-wide">Cron</div>
              <div className="font-mono text-lg">{schedule || "—"}</div>
              {schedule ? (
                <div className="text-muted-foreground text-sm">{describe(schedule)}</div>
              ) : null}
              {workload.concurrencyPolicy ? (
                <div className="text-muted-foreground pt-1 text-xs">
                  Concurrency: <span className="font-medium">{workload.concurrencyPolicy}</span>
                </div>
              ) : null}
            </div>
            <div className="space-y-1">
              <div className="text-muted-foreground text-xs uppercase tracking-wide">Next run</div>
              {next ? (
                <>
                  <div className="text-2xl font-semibold tabular-nums">
                    {now != null ? formatCountdown(next.getTime() - now) : "—"}
                  </div>
                  <div className="text-muted-foreground text-sm" title={next.toISOString()}>
                    {next.toLocaleString()}
                  </div>
                </>
              ) : (
                <div className="text-muted-foreground text-sm">
                  {schedule ? "Couldn't compute the next run for this expression." : "—"}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <HistoryIcon className="size-4" />
              Run history
            </CardTitle>
          </CardHeader>
          <CardContent>
            <EmptyState
              icon={<ScrollTextIcon className="size-5" />}
              title="No runs recorded yet"
              description="Each scheduled execution will appear here. In the meantime, open the workload for its manifest, config, and pod logs."
              secondary={
                <Link
                  href={`/apps/${slug}/workloads/${encodeURIComponent(workload.slug)}`}
                  className="text-sm text-[var(--brand-primary)] hover:underline"
                >
                  Open workload
                </Link>
              }
            />
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
