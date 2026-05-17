"use client";

/**
 * MetricScopePicker — environment + workload dropdowns that scope
 * the App > Observability ``GoldenSignalsPanel`` (#422).
 *
 * Two selects:
 *
 *  - **Environment** — sourced from ``LIST_ENVIRONMENTS({appSlug})``.
 *    The Prometheus query layer already accepts ``environmentName``
 *    (#380). Default: first env alphabetically — preserves the
 *    pre-#422 backend behavior where the resolver picks the
 *    alphabetically-first env when none is supplied.
 *  - **Workload** — sourced from ``LIST_WORKLOADS({appSlug})``.
 *    Default: "All workloads" (no ``workloadSlug`` arg → resolver
 *    rolls every workload up).
 *
 * The picker is a controlled component — the parent owns the
 * ``environmentName`` / ``workloadSlug`` state and threads it into
 * the URL query string so deep-links survive refresh.
 *
 * Loading + empty cases:
 *
 *  - While the env list is loading, render disabled selects with a
 *    placeholder.
 *  - If the app has zero environments, hide the env picker (the
 *    backend will still roll up — no decision to make).
 *  - If the app has zero workloads, hide the workload picker.
 */

import { useQuery } from "@apollo/client/react";
import { LayersIcon, GlobeIcon } from "lucide-react";
import * as React from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/__generated__/schema";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

const ALL_WORKLOADS_VALUE = "__all__";

interface EnvResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface WorkloadResp {
  astroliftWorkloads: AstroliftWorkload[];
}

export interface MetricScopePickerProps {
  appSlug: string;
  /** Current env selection — ``null`` means "let the backend pick the
   *  alphabetically-first env" (pre-#422 default). */
  environmentName: string | null;
  /** Current workload selection — ``null`` means "all workloads"
   *  (no PromQL ``workload=`` matcher). */
  workloadSlug: string | null;
  /** Notify on change. Parent persists the choice to ``?env=`` /
   *  ``?workload=`` query params. */
  onEnvironmentChange: (name: string | null) => void;
  onWorkloadChange: (slug: string | null) => void;
  /**
   * Optional labels — defaults wired in :file:`observability-client.tsx`
   * via `next-intl`. Exposed so this component stays usable in stories
   * + future surfaces without depending on the messages dictionary.
   */
  labels?: {
    environment: string;
    workload: string;
    allWorkloads: string;
    environmentPlaceholder: string;
    workloadPlaceholder: string;
  };
}

const DEFAULT_LABELS = {
  environment: "Environment",
  workload: "Workload",
  allWorkloads: "All workloads",
  environmentPlaceholder: "Select an environment",
  workloadPlaceholder: "Select a workload",
} as const;

export function MetricScopePicker({
  appSlug,
  environmentName,
  workloadSlug,
  onEnvironmentChange,
  onWorkloadChange,
  labels = DEFAULT_LABELS,
}: MetricScopePickerProps) {
  const envs = useQuery<EnvResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const workloads = useQuery<WorkloadResp>(LIST_WORKLOADS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });

  // Sort env list alphabetically so the default ("first env") is
  // deterministic across renders. Backend resolver applies the same
  // rule when ``environmentName`` is null.
  const sortedEnvs = React.useMemo(() => {
    const rows = envs.data?.astroliftEnvironments ?? [];
    return [...rows].sort((a, b) => a.name.localeCompare(b.name));
  }, [envs.data]);

  const sortedWorkloads = React.useMemo(() => {
    const rows = workloads.data?.astroliftWorkloads ?? [];
    return [...rows].sort((a, b) => a.name.localeCompare(b.name));
  }, [workloads.data]);

  // Effective env: if the parent didn't pick one yet, default to the
  // alphabetically-first env (so the UI label matches what the backend
  // resolved against). Note we do NOT call ``onEnvironmentChange`` —
  // that would clobber a deep-link in flight.
  const effectiveEnv = environmentName ?? sortedEnvs[0]?.name ?? null;

  // Workloads are conditional on env selection in many apps (an env
  // may not declare every workload). The data model is currently
  // app-scoped not env-scoped, so we don't filter here — the issue
  // calls for env-scoped filtering when the data shape grows. For
  // v1, the workload picker lists every workload under the app.

  const envsLoading = envs.loading && sortedEnvs.length === 0;
  const workloadsLoading = workloads.loading && sortedWorkloads.length === 0;

  if (envsLoading && workloadsLoading) {
    return (
      <div className="flex items-center gap-2">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-8 w-40" />
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {sortedEnvs.length > 1 && (
        <div className="flex items-center gap-1.5">
          <GlobeIcon className="text-muted-foreground size-3.5" aria-hidden="true" />
          <Select
            value={effectiveEnv ?? undefined}
            onValueChange={(v) => onEnvironmentChange(v || null)}
          >
            <SelectTrigger
              size="sm"
              aria-label={labels.environment}
              className="h-8 min-w-[10rem] text-xs"
            >
              <SelectValue placeholder={labels.environmentPlaceholder} />
            </SelectTrigger>
            <SelectContent>
              {sortedEnvs.map((env) => (
                <SelectItem key={env.id} value={env.name}>
                  {env.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      {sortedWorkloads.length > 0 && (
        <div className="flex items-center gap-1.5">
          <LayersIcon className="text-muted-foreground size-3.5" aria-hidden="true" />
          <Select
            value={workloadSlug ?? ALL_WORKLOADS_VALUE}
            onValueChange={(v) => onWorkloadChange(v === ALL_WORKLOADS_VALUE ? null : v)}
          >
            <SelectTrigger
              size="sm"
              aria-label={labels.workload}
              className="h-8 min-w-[10rem] text-xs"
            >
              <SelectValue placeholder={labels.workloadPlaceholder} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_WORKLOADS_VALUE}>{labels.allWorkloads}</SelectItem>
              {sortedWorkloads.map((w) => (
                <SelectItem key={w.id} value={w.slug}>
                  {w.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}
