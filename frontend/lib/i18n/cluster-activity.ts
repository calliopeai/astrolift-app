"use client";

import { useNow, useTranslations } from "next-intl";
import { useFormatters } from "./formatters";

const STATUS_LABELS = {
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELED: "canceled",
  TERMINATED: "terminated",
  TIMED_OUT: "timedOut",
  CONTINUED_AS_NEW: "continuedAsNew",
} as const;
const STATUS_VARIANTS = {
  RUNNING: "secondary",
  COMPLETED: "default",
  FAILED: "destructive",
  CANCELED: "outline",
  TERMINATED: "destructive",
  TIMED_OUT: "destructive",
  CONTINUED_AS_NEW: "outline",
} as const;
const OPERATIONS = {
  bringClusterIntoManagement: "bring",
  BringClusterIntoManagement: "bring",
  refreshClusterManagement: "refresh",
  RefreshClusterManagement: "refresh",
  decommissionCluster: "decommission",
  DecommissionCluster: "decommission",
  installClusterPrereqs: "prereqs",
  InstallClusterPrereqs: "prereqs",
  DriftDetection: "drift",
  registerTenantCluster: "register",
  registerCluster: "register",
  unregisterTenantCluster: "unregister",
} as const;

function observedDate(value: unknown): Date | null {
  if (typeof value !== "string" || !value) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}

export function useClusterActivity() {
  const t = useTranslations("clusterActivity");
  const fmt = useFormatters();
  const now = useNow();
  function seconds(startedAt: unknown, closedAt: unknown) {
    const started = observedDate(startedAt);
    const closed = observedDate(closedAt);
    return !started || !closed || closed.getTime() < started.getTime()
      ? null
      : Math.round((closed.getTime() - started.getTime()) / 1000);
  }
  return {
    t,
    status: (value: unknown) => {
      const token = typeof value === "string" ? value : "";
      const known = Object.hasOwn(STATUS_LABELS, token);
      return {
        token,
        label: known
          ? t(STATUS_LABELS[token as keyof typeof STATUS_LABELS])
          : token || t("unknown"),
        variant: known
          ? STATUS_VARIANTS[token as keyof typeof STATUS_VARIANTS]
          : ("outline" as const),
      };
    },
    operation: (value: unknown) => {
      const token = typeof value === "string" ? value : "";
      return Object.hasOwn(OPERATIONS, token)
        ? t(OPERATIONS[token as keyof typeof OPERATIONS])
        : token || t("unknown");
    },
    date: (value: unknown) => {
      const date = observedDate(value);
      return date ? fmt.formatDateTime(date) : value ? t("unknown") : "—";
    },
    relative: (value: unknown) => {
      const date = observedDate(value);
      return date ? fmt.formatRelativeTime(date, now) : value ? t("unknown") : "—";
    },
    duration: (startedAt: unknown, closedAt: unknown, compact = false) => {
      if (!closedAt) return null;
      const observed = seconds(startedAt, closedAt);
      if (observed === null) return t("unknown");
      if (!compact || observed < 60) return t("seconds", { count: observed });
      const minutes = Math.floor(observed / 60);
      const remainder = observed % 60;
      if (minutes < 60)
        return remainder
          ? t("minutesSeconds", { minutes, seconds: remainder })
          : t("minutes", { count: minutes });
      const hours = Math.floor(minutes / 60);
      const remainderMinutes = minutes % 60;
      return remainderMinutes
        ? t("hoursMinutes", { hours, minutes: remainderMinutes })
        : t("hours", { count: hours });
    },
  };
}
