"use client";

import { useTranslations } from "next-intl";

import { StatusDot } from "@/components/StatusDot";
import { useFormatters } from "@/lib/i18n/formatters";

import type { EdgeStatus } from "./edge-access-list";

const DOT: Record<EdgeStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  enforced: "ok",
  applying: "pending",
  pending_deploy: "warn",
  unverified: "warn",
  drift: "error",
  open: "muted",
  not_on_edge: "muted",
};

/**
 * Whether the edge holds a row's rule (#2534): the status, and under it what
 * it means or what fixes it. Enforced names when the edge last read the rules.
 */
export function EdgeStatusCell({
  status,
  appliedAt,
}: {
  status: EdgeStatus;
  appliedAt: string | null;
}) {
  const t = useTranslations("adminAppAccess.status");
  const fmt = useFormatters();
  const hint =
    status === "enforced" && appliedAt
      ? t("appliedAt", { time: fmt.formatDateTime(appliedAt) })
      : t(`hints.${status}`);
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="inline-flex items-center gap-1.5 font-mono text-xs">
        <StatusDot status={DOT[status]} />
        {t(`labels.${status}`)}
      </span>
      <span className="text-muted-foreground truncate text-xs" title={hint}>
        {hint}
      </span>
    </div>
  );
}
