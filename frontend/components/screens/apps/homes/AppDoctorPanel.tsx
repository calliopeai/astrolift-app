"use client";

/**
 * AppDoctorPanel (#1550) — answers "is this app fully wired?" in one place.
 *
 * The service behind this has existed and been tested since #1550 was filed,
 * and nothing called it: the checks were written, green, and unreachable, so
 * the question an operator actually asks had no answer in the product. The
 * scattered pieces it replaces are still worth knowing about — `autowire`
 * covers the repo half (webhook, CI file, deploy secret) and this covers the
 * cloud half.
 *
 * The pure view; useAppDoctor holds the probe and the repairs.
 */

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CircleHelpIcon,
  Loader2Icon,
  MinusCircleIcon,
  StethoscopeIcon,
  XCircleIcon,
} from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { Button } from "@/components/ui/button";

import { REPAIRS, type useAppDoctor } from "./use-app-doctor";

export type AppDoctorPanelViewProps = ReturnType<typeof useAppDoctor>;

/** Human labels for the service's check keys. */
// Falls back to the raw key, which is why three checks added since this map
// was written have been rendering as `cronjob_runs` and `registry_repo`.
const CHECK_LABELS: Record<string, string> = {
  manifest: "Manifest",
  autowire: "Repo wiring",
  registry_repo: "Container registry",
  push_role: "Push role",
  dns: "DNS",
  identity: "Pod identity",
  image: "Image pinning",
  deployments: "Deployments",
  cronjob_runs: "Scheduled runs",
  managed_services: "Managed services",
};

/**
 * Per-status presentation. `unknown` is deliberately distinct from `fail`:
 * the probe itself errored, so the dependency is unverified rather than
 * known-broken, and telling an operator "broken" when we mean "we could not
 * look" sends them after the wrong thing.
 */
const STATUS_PRESENTATION: Record<
  string,
  { icon: React.ComponentType<{ className?: string }>; className: string; label: string }
> = {
  pass: { icon: CheckCircle2Icon, className: "text-success-fg", label: "ok" },
  fail: { icon: XCircleIcon, className: "text-danger-fg", label: "failing" },
  warn: { icon: AlertTriangleIcon, className: "text-warning-fg", label: "warning" },
  skip: { icon: MinusCircleIcon, className: "text-muted-foreground", label: "not applicable" },
  unknown: { icon: CircleHelpIcon, className: "text-muted-foreground", label: "unverified" },
};

function RepairButton({
  verb,
  running,
  onRun,
}: {
  verb: string;
  running: string | null;
  onRun: (verb: string) => void;
}) {
  const repair = REPAIRS[verb];
  // An unrecognised verb renders nothing rather than a dead button. The
  // backend's own guard keeps `fix` inside this vocabulary, so reaching here
  // means the two drifted -- and a button that cannot act is worse than no
  // button, which is the defect this panel exists to surface.
  if (!repair) return null;

  return (
    <Can permission={repair.permission}>
      <Button
        size="sm"
        variant="outline"
        className="mt-1 h-6 gap-1 px-2 text-xs"
        disabled={running !== null}
        onClick={() => onRun(verb)}
      >
        {running === verb ? <Loader2Icon className="size-3 animate-spin" /> : null}
        {repair.label}
      </Button>
    </Can>
  );
}

export function AppDoctorPanelView({
  report,
  loading,
  errorMessage,
  called,
  runChecks,
  repair,
  running,
}: AppDoctorPanelViewProps) {
  return (
    <div className="border-border flex flex-col gap-3 rounded-md border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-medium">
            <StethoscopeIcon className="size-4" />
            App doctor
          </p>
          <p className="text-muted-foreground mt-0.5 text-xs">
            Checks each dependency this app needs and reports whether it is actually usable. Runs
            live probes, so it only runs when you ask.
          </p>
        </div>
        <Can permission="app.read">
          <Button
            size="sm"
            variant="outline"
            onClick={runChecks}
            disabled={loading}
            className="gap-1.5"
          >
            {loading ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <StethoscopeIcon className="size-3.5" />
            )}
            {called ? "Run again" : "Run checks"}
          </Button>
        </Can>
      </div>

      {errorMessage ? <p className="text-danger-fg text-xs">{errorMessage}</p> : null}

      {report ? (
        <>
          <p className="text-xs font-medium">
            {report.healthy ? (
              <span className="text-success-fg">Everything this app needs checks out.</span>
            ) : (
              <span className="text-warning-fg">Some dependencies need attention — see below.</span>
            )}
          </p>
          <ul className="flex flex-col gap-2">
            {report.checks.map((check) => {
              const presentation = STATUS_PRESENTATION[check.status] ?? STATUS_PRESENTATION.unknown;
              const Icon = presentation.icon;
              return (
                <li key={check.key} className="flex items-start gap-2 text-xs">
                  <Icon className={`mt-0.5 size-3.5 shrink-0 ${presentation.className}`} />
                  <span className="min-w-0">
                    <span className="font-medium">{CHECK_LABELS[check.key] ?? check.key}</span>
                    <span className="text-muted-foreground"> — {presentation.label}</span>
                    {check.detail ? (
                      <span className="text-muted-foreground block">{check.detail}</span>
                    ) : null}
                    {check.fix ? (
                      <RepairButton
                        verb={check.fix}
                        running={running}
                        onRun={(verb) => void repair(verb)}
                      />
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

      {called && !loading && !errorMessage && report && report.checks.length === 0 ? (
        <p className="text-muted-foreground text-xs">Nothing to report for this app.</p>
      ) : null}
    </div>
  );
}
