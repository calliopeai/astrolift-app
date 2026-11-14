"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { PILL_TONE, StatusBadge, type StatusTone } from "@calliopelabs/ui";
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

const STATUS_TONE: Record<RunStatus, StatusTone> = {
  running: "info",
  in_progress: "info",
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
  const tone = STATUS_TONE[status] ?? "muted";
  const inFlight = status === "running" || status === "in_progress";
  const label = labelFor(status, t);
  // Exit code chip only when we have a concrete non-zero value —
  // ``null`` means in-flight, ``0`` is the silent success case.
  const showExit = typeof exitCode === "number" && exitCode !== 0;
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <StatusBadge tone={tone} pulse={inFlight} className="capitalize">
        {label}
      </StatusBadge>
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
