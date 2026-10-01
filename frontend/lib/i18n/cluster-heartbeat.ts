"use client";

import { useTranslations } from "next-intl";
import { heartbeatPresentation } from "@/lib/cluster-heartbeat";

type Copy = ReturnType<typeof useTranslations<"clusterConnection">>;
const labels = {
  connected: "connected",
  degraded: "degraded",
  offline: "offline",
  never_seen: "noAgent",
} as const;

export function heartbeatAge(age: number | null, t: Copy): string | null {
  if (age === null || !Number.isFinite(age) || age < 0) return null;
  const seconds = Math.floor(age);
  if (seconds < 5) return t("justNow");
  if (seconds < 60) return t("secondsAgo", { count: seconds });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("minutesAgo", { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("hoursAgo", { count: hours });
  return t("daysAgo", { count: Math.floor(hours / 24) });
}

export function useClusterHeartbeat(status: string, seconds: number | null) {
  const t = useTranslations("clusterConnection");
  const age = heartbeatAge(seconds, t);
  return {
    ...heartbeatPresentation(status),
    label: Object.hasOwn(labels, status)
      ? t(labels[status as keyof typeof labels])
      : status || t("unknown"),
    age,
    message:
      status === "never_seen"
        ? t("noAgentHelp")
        : status === "offline"
          ? age
            ? t("offlineAge", { age })
            : t("offlineNoAge")
          : t("unknownHelp"),
  };
}
