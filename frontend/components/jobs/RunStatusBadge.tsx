"use client";

import { useTranslations } from "next-intl";

import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { PILL_TONE, type StatusTone } from "@/lib/status-tones";
import { cn } from "@/lib/utils";

/**
 * #427 — shared run-status surface across the global jobs table,
 * the per-app jobs table, and the commands tab. Each row gets the
 * same {dot, label badge, optional exit-code chip} so an operator
 * scanning a list reads each cell the same way regardless of the
 * surface they came from. Pulling this into one place means a
 * future status addition (e.g. "queued") only needs a single edit.
 */

export type RunStatus =
  // ScheduledJobRun statuses
  | "running"
  | "succeeded"
  | "failed"
  | "superseded"
  // CommandRun synthetic statuses (derived from exit_code + ended_at)
  | "in_progress"
  | "unknown";

type Dot = "ok" | "warn" | "error" | "muted" | "pending";

const DOT_PILL: Record<Dot, StatusTone> = {
  ok: "ok",
  warn: "warn",
  error: "error",
  muted: "muted",
  pending: "info",
};

const STATUS_DOT: Record<RunStatus, Dot> = {
  running: "pending",
  in_progress: "pending",
  succeeded: "ok",
  failed: "error",
  superseded: "muted",
  unknown: "muted",
};

export interface RunStatusBadgeProps {
  status: RunStatus;
  /** Container / process exit code from the underlying run, if known. */
  exitCode?: number | null;
  className?: string;
}

export function RunStatusBadge({ status, exitCode, className }: RunStatusBadgeProps) {
  const t = useTranslations("jobs.runStatus");
  const dot = STATUS_DOT[status] ?? "muted";
  const label = labelFor(status, t);
  // Exit code chip only when we have a concrete non-zero value —
  // ``null`` means in-flight, ``0`` is the silent success case.
  const showExit = typeof exitCode === "number" && exitCode !== 0;
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <StatusDot status={dot} />
      <Badge variant="outline" className={cn("capitalize", PILL_TONE[DOT_PILL[dot]])}>
        {label}
      </Badge>
      {showExit ? (
        <Badge
          variant="outline"
          className={cn("font-mono", PILL_TONE.error)}
          title={t("exitCodeTooltip", { code: exitCode })}
        >
          {t("exitCodeLabel", { code: exitCode })}
        </Badge>
      ) : null}
    </span>
  );
}

function labelFor(
  status: RunStatus,
  t: ReturnType<typeof useTranslations<"jobs.runStatus">>
): string {
  switch (status) {
    case "running":
      return t("running");
    case "in_progress":
      return t("inProgress");
    case "succeeded":
      return t("succeeded");
    case "failed":
      return t("failed");
    case "superseded":
      return t("superseded");
    case "unknown":
    default:
      return t("unknown");
  }
}

/**
 * Derive a {@link RunStatus} for a command run (which only carries
 * exit_code + ended_at, no explicit status string). Mirrors the
 * convention from the existing /jobs surface: in-flight while
 * ``ended_at`` is null, ``succeeded``/``failed`` once the run has
 * terminated.
 */
export function commandRunStatus(opts: {
  endedAt: string | null | undefined;
  exitCode: number | null | undefined;
}): RunStatus {
  if (!opts.endedAt) return "in_progress";
  if (typeof opts.exitCode !== "number") return "unknown";
  return opts.exitCode === 0 ? "succeeded" : "failed";
}
