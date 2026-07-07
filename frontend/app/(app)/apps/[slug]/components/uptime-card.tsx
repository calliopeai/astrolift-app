"use client";

import { gql } from "@apollo/client";
import { useQuery } from "@apollo/client/react";
import { ActivityIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { Sparkline } from "@/components/viz";

// Hand-typed (not codegen'd): the astroliftAppUptime query is answered by the
// deployed backend at runtime; mirrors the pipeline-detail gql pattern.
const GET_APP_UPTIME = gql`
  query GetAppUptime($appSlug: String!) {
    astroliftAppUptime(appSlug: $appSlug) {
      isUp
      lastCheckedAt
      uptimePct
      totalChecks
      windowHours
      recent {
        checkedAt
        isUp
        latencyMs
        statusCode
      }
    }
  }
`;

interface UptimePoint {
  checkedAt: string;
  isUp: boolean;
  latencyMs: number;
  statusCode: number | null;
}
interface AppUptime {
  isUp: boolean | null;
  lastCheckedAt: string | null;
  uptimePct: number;
  totalChecks: number;
  windowHours: number;
  recent: UptimePoint[];
}
interface Resp {
  astroliftAppUptime: AppUptime | null;
}

export function UptimeCard({ appSlug }: { appSlug: string }) {
  const { data, loading } = useQuery<Resp>(GET_APP_UPTIME, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
    pollInterval: 60000,
  });
  const u = data?.astroliftAppUptime ?? null;

  if (loading && !u) {
    return (
      <Card>
        <CardContent className="p-4">
          <Skeleton className="h-14 w-full" />
        </CardContent>
      </Card>
    );
  }

  // No datapoints yet — the prober checks every ~2 min. Show a neutral hint,
  // not a scary "down".
  if (!u || u.totalChecks === 0) {
    return (
      <Card>
        <CardContent className="text-muted-foreground flex items-center gap-2 p-4 text-sm">
          <ActivityIcon className="size-4" />
          Uptime monitoring is active — the first result appears within a couple of minutes.
        </CardContent>
      </Card>
    );
  }

  const up = u.isUp === true;
  const latencies = u.recent.map((p) => p.latencyMs);
  const lastChecked = u.lastCheckedAt ? new Date(u.lastCheckedAt) : null;

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
        <div className="flex items-center gap-3">
          <Badge
            variant="outline"
            className={cn(
              "gap-1",
              up ? "bg-success/15 text-success-fg" : "bg-danger/15 text-danger-fg",
            )}
          >
            <span
              className={cn("size-1.5 rounded-full", up ? "bg-success" : "bg-danger animate-pulse")}
            />
            {up ? "Up" : "Down"}
          </Badge>
          <div className="text-sm">
            <span className="font-semibold tabular-nums">{u.uptimePct}%</span>{" "}
            <span className="text-muted-foreground">uptime · last {u.windowHours}h</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {lastChecked && (
            <span className="text-muted-foreground text-2xs">
              checked {lastChecked.toLocaleTimeString()}
            </span>
          )}
          {latencies.length > 1 && (
            <div className="flex items-center gap-1.5">
              <span className="text-muted-foreground text-2xs tracking-wide uppercase">Latency</span>
              <Sparkline
                data={latencies}
                width={150}
                height={30}
                variant="area"
                className={up ? "text-success-fg" : "text-danger-fg"}
                ariaLabel="Probe latency, recent"
              />
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
