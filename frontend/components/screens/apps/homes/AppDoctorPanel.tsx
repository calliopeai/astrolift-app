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
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { Panel, type PanelSpan } from "@/components/panel/Panel";
import { Button } from "@/components/ui/button";

import { REPAIRS, type useAppDoctor } from "./use-app-doctor";

export type AppDoctorPanelViewProps = ReturnType<typeof useAppDoctor> & { span?: PanelSpan };

/** The service's check keys with a human label under `appHomes.doctor.checks`. */
// Falls back to the raw key, which is why three checks added since this map
// was written have been rendering as `cronjob_runs` and `registry_repo`.
const CHECK_KEYS = new Set([
  "manifest",
  "autowire",
  "registry_repo",
  "push_role",
  "dns",
  "identity",
  "image",
  "deployments",
  "cronjob_runs",
  "managed_services",
]);

/**
 * Per-status presentation. `unknown` is deliberately distinct from `fail`:
 * the probe itself errored, so the dependency is unverified rather than
 * known-broken, and telling an operator "broken" when we mean "we could not
 * look" sends them after the wrong thing.
 */
const STATUS_PRESENTATION: Record<
  string,
  { icon: React.ComponentType<{ className?: string }>; className: string }
> = {
  pass: { icon: CheckCircle2Icon, className: "text-success-fg" },
  fail: { icon: XCircleIcon, className: "text-danger-fg" },
  warn: { icon: AlertTriangleIcon, className: "text-warning-fg" },
  skip: { icon: MinusCircleIcon, className: "text-muted-foreground" },
  unknown: { icon: CircleHelpIcon, className: "text-muted-foreground" },
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
  const t = useTranslations("appHomes.doctor.repairs");
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
        {t(`${verb}.label`)}
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
  span = 12,
}: AppDoctorPanelViewProps) {
  const t = useTranslations("appHomes.doctor");
  return (
    <Panel
      title={t("title")}
      icon={<StethoscopeIcon className="size-4" />}
      description={t("description")}
      span={span}
      loading={loading && !report}
      error={errorMessage || null}
      onRetry={runChecks}
      actions={
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
            {called ? t("runAgain") : t("runChecks")}
          </Button>
        </Can>
      }
    >
      <div className="flex min-w-0 flex-col gap-3">
        {!called && !report ? <p className="text-muted-foreground text-xs">{t("notRun")}</p> : null}

        {report ? (
          <>
            <p className="text-xs font-medium">
              {report.healthy ? (
                <span className="text-success-fg">{t("healthy")}</span>
              ) : (
                <span className="text-warning-fg">{t("unhealthy")}</span>
              )}
            </p>
            <ul className="flex flex-col gap-2">
              {report.checks.map((check) => {
                const statusKey = check.status in STATUS_PRESENTATION ? check.status : "unknown";
                const presentation = STATUS_PRESENTATION[statusKey];
                const Icon = presentation.icon;
                return (
                  <li key={check.key} className="flex items-start gap-2 text-xs">
                    <Icon className={`mt-0.5 size-3.5 shrink-0 ${presentation.className}`} />
                    <span className="min-w-0">
                      <span className="font-medium">
                        {CHECK_KEYS.has(check.key) ? t(`checks.${check.key}`) : check.key}
                      </span>
                      <span className="text-muted-foreground"> · {t(`status.${statusKey}`)}</span>
                      {check.detail ? (
                        <span className="text-muted-foreground block font-mono [overflow-wrap:anywhere]">
                          {check.detail}
                        </span>
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
          <p className="text-muted-foreground text-xs">{t("nothingToReport")}</p>
        ) : null}
      </div>
    </Panel>
  );
}
