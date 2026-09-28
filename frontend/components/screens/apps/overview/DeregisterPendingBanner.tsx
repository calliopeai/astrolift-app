"use client";

/**
 * DeregisterPendingBannerView (#436 B) — grace-period cancel surface.
 *
 * Pinned banner that appears at the top of the app detail page after a
 * successful deregister kickoff. Renders a live countdown of the 5-min
 * grace window during which the operator can cancel a typo'd confirm
 * before any destructive activity runs. Renders nothing when no window
 * is open (`msRemaining` null or elapsed).
 */

import { CircleSlashIcon, Loader2Icon, ShieldAlertIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";

import type { useDeregisterPending } from "./use-deregister-pending";

export type DeregisterPendingBannerViewProps = ReturnType<typeof useDeregisterPending>;

function formatCountdown(msRemaining: number): string {
  if (msRemaining <= 0) return "0:00";
  const totalSeconds = Math.ceil(msRemaining / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export function DeregisterPendingBannerView({
  msRemaining,
  cancelling,
  onCancel,
}: DeregisterPendingBannerViewProps) {
  const t = useTranslations("apps.dangerZone.pendingBanner");

  if (msRemaining == null || msRemaining <= 0) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="border-warning-border bg-warning/10 text-warning-fg flex items-center gap-3 rounded-md border p-3"
    >
      <ShieldAlertIcon className="size-5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{t("title")}</p>
        <p className="text-xs opacity-80">
          {t("body", { countdown: formatCountdown(msRemaining) })}
        </p>
      </div>
      <Button
        size="sm"
        variant="outline"
        className="border-warning-border hover:bg-warning/20"
        onClick={() => void onCancel()}
        disabled={cancelling}
      >
        {cancelling ? (
          <Loader2Icon className="size-4 animate-spin" />
        ) : (
          <CircleSlashIcon className="size-4" />
        )}
        {t("cancel")}
      </Button>
    </div>
  );
}
