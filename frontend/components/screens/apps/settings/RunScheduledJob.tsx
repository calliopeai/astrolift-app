"use client";

import { Loader2Icon, PlayCircleIcon, TimerIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import type { useRunScheduledJob } from "./use-run-scheduled-job";

export type RunScheduledJobViewProps = ReturnType<typeof useRunScheduledJob> & {
  /** `/apps` or `/agents`: the prefix the jobs link navigates under. */
  basePath: string;
};

/**
 * "Run scheduled job once" card on the settings landing (#390).
 *
 * Lists the app's manifest-declared cronjob workloads and dispatches
 * a single ad-hoc k8s Job built from the selected workload's
 * jobTemplate. The schedule is untouched — recurring runs continue
 * on their own. The card hides entirely when the manifest has zero
 * cronjob workloads so the surface stays uncluttered for apps that
 * don't ship any.
 *
 * Permission gate matches the backend: ``app.deploy`` is required to
 * land the Job on the tenant cluster.
 */
export function RunScheduledJobView({
  appSlug,
  cronJobs,
  environments,
  workloadsLoading,
  running,
  onRun,
  basePath,
}: RunScheduledJobViewProps) {
  const t = useTranslations("apps.settings.runJob");
  const [jobSlugOverride, setJobSlugOverride] = React.useState<string | null>(null);
  const [envNameOverride, setEnvNameOverride] = React.useState<string | null>(null);
  // Default to the first entry of each list — keeps the card actionable
  // for the common single-cronjob, single-env app without an extra
  // click. The operator's explicit selection (when one exists) wins.
  const jobSlug = jobSlugOverride ?? cronJobs[0]?.slug ?? "";
  const envName = envNameOverride ?? environments[0]?.name ?? "";

  // Hide the card while the workloads query is still in flight on
  // the first load — flashing an empty card then a populated one
  // is worse than waiting a tick. Hide it permanently when the
  // manifest has zero cronjob workloads.
  if (workloadsLoading) {
    return null;
  }
  if (cronJobs.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
          <TimerIcon className="size-5" />
        </div>
        <div className="flex-1">
          <CardTitle className="text-base">{t("title")}</CardTitle>
          <CardDescription className="mt-1">{t("description")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <div className="grid gap-1.5">
            <Label htmlFor="run-job-job-slug" className="text-xs">
              {t("job")}
            </Label>
            <Select value={jobSlug} onValueChange={setJobSlugOverride}>
              <SelectTrigger id="run-job-job-slug" className="w-full">
                <SelectValue placeholder={t("selectJob")} />
              </SelectTrigger>
              <SelectContent>
                {cronJobs.map((w) => (
                  <SelectItem key={w.slug} value={w.slug}>
                    <span className="font-mono text-xs">{w.slug}</span>
                    {w.schedule ? (
                      <span className="text-muted-foreground text-2xs ml-2">{w.schedule}</span>
                    ) : null}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="run-job-env" className="text-xs">
              {t("environment")}
            </Label>
            <Select value={envName} onValueChange={setEnvNameOverride}>
              <SelectTrigger id="run-job-env" className="w-full">
                <SelectValue placeholder={t("selectEnv")} />
              </SelectTrigger>
              <SelectContent>
                {environments.map((e) => (
                  <SelectItem key={e.id} value={e.name}>
                    {e.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Can permission="app.deploy">
            <Button
              onClick={() => void onRun(jobSlug, envName)}
              disabled={running || !jobSlug || !envName}
              className="sm:self-end"
            >
              {running ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <PlayCircleIcon className="size-4" />
              )}
              {t("run")}
            </Button>
          </Can>
        </div>
        <p className="text-muted-foreground text-2xs mt-3">
          {t("footer")}{" "}
          <Link href={`${basePath}/${appSlug}/jobs`} className="underline">
            {t("jobsPage")}
          </Link>
          .
        </p>
      </CardContent>
    </Card>
  );
}
