"use client";

import { ActivityIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Panel, type PanelSpan } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";
import { Sparkline } from "@/components/viz";
import { cn } from "@/lib/utils";

import type { useUptime } from "./use-uptime";

export type UptimeCardViewProps = Partial<Pick<ReturnType<typeof useUptime>, "error" | "onRetry">> &
  Pick<ReturnType<typeof useUptime>, "uptime" | "loading"> & {
    span?: PanelSpan;
  };

/**
 * The overview's Health panel: up or down, uptime over the probe window and
 * the recent probe latency. A down app says so in the panel's failure strip.
 */
export function UptimeCardView({
  uptime: u,
  loading,
  error,
  onRetry,
  span = 6,
}: UptimeCardViewProps) {
  const t = useTranslations("apps.overview.health");
  const up = u?.isUp === true;
  const hasData = !!u && u.totalChecks > 0;
  const latencies = u?.recent.map((p) => p.latencyMs) ?? [];
  const lastChecked = u?.lastCheckedAt ?? null;

  return (
    <Panel
      title={t("title")}
      icon={<ActivityIcon className="size-4" />}
      span={span}
      loading={loading && !u}
      error={error}
      onRetry={onRetry}
      failure={
        hasData && u.isUp === false
          ? {
              title: t("down"),
              reason: t("downReason", { pct: u.uptimePct, hours: u.windowHours }),
            }
          : null
      }
    >
      {!hasData ? (
        // The prober checks every ~2 min: a fresh app says so, not "down".
        <p className="text-muted-foreground text-sm">{t("waiting")}</p>
      ) : (
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex items-center gap-1.5 text-sm font-medium">
              <StatusDot status={up ? "ok" : "error"} />
              {up ? t("up") : t("down")}
            </span>
            <span className="min-w-0 text-sm [overflow-wrap:anywhere]">
              <span className="font-mono font-semibold tabular-nums">{u.uptimePct}%</span>{" "}
              <span className="text-muted-foreground">{t("window", { hours: u.windowHours })}</span>
            </span>
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            {lastChecked && (
              <span className="text-muted-foreground text-2xs" title={lastChecked}>
                {t("checked")}{" "}
                <span className="font-mono">{new Date(lastChecked).toLocaleTimeString()}</span>
              </span>
            )}
            {latencies.length > 1 && (
              <span className="flex items-center gap-1.5">
                <span className="text-muted-foreground text-2xs tracking-wide uppercase">
                  {t("latency")}
                </span>
                <Sparkline
                  data={latencies}
                  width={150}
                  height={30}
                  variant="area"
                  className={cn(up ? "text-success-fg" : "text-danger-fg")}
                  ariaLabel={t("latencyAria")}
                />
              </span>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}
