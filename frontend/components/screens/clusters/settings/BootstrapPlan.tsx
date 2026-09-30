"use client";

import { Loader2Icon, PlayIcon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";

import { preselected } from "./types";
import type { useBootstrapPlan } from "./use-bootstrap";

export type BootstrapPlanViewProps = ReturnType<typeof useBootstrapPlan>;

/**
 * Bootstrap plan card (#67 + #66). Renders the driver's bootstrap recipe
 * as an interactive checklist. Operator picks components + option values;
 * clicking Install fires InstallClusterPrereqsWorkflow which applies one
 * Flux HelmRelease per chosen component to ``astrolift-system``.
 * Idempotent — re-running with a different selection converges.
 */
export function BootstrapPlanView({
  plan,
  loading,
  installing,
  onInstall,
}: BootstrapPlanViewProps) {
  const [draft, setDraft] = React.useState<{
    clusterId: string | null;
    selected: Record<string, boolean>;
    options: Record<string, Record<string, string>>;
  }>({ clusterId: plan?.clusterId ?? null, selected: {}, options: {} });
  if (plan && draft.clusterId !== plan.clusterId) {
    setDraft({ clusterId: plan.clusterId, selected: {}, options: {} });
  }
  const sameCluster = plan?.clusterId === draft.clusterId;
  const selected: Record<string, boolean> = {};
  const optionValues: Record<string, Record<string, string>> = {};
  for (const component of plan?.components ?? []) {
    selected[component.key] = sameCluster
      ? (draft.selected[component.key] ?? preselected(component))
      : preselected(component);
    optionValues[component.key] = {};
    for (const option of component.options) {
      const picked = sameCluster ? draft.options[component.key]?.[option.key] : undefined;
      optionValues[component.key][option.key] =
        picked && option.choices.some((c) => c.value === picked)
          ? picked
          : option.default || option.choices[0]?.value || "";
    }
  }

  if (loading) {
    return (
      <Section title="Bootstrap recipe" divided>
        <div className="flex min-w-0 flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={`bootstrap-skel-${i}`}
              className="flex items-start gap-3 rounded-md border p-3"
            >
              <Skeleton className="mt-1 size-4 rounded-sm" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-3/4" />
              </div>
            </div>
          ))}
        </div>
      </Section>
    );
  }
  if (!plan || plan.components.length === 0) {
    return (
      <Section
        title="Bootstrap recipe"
        description={
          <>
            No driver recipe available for this provider. Install platform prerequisites manually or
            via the <code className="font-mono text-xs">astro cluster bootstrap</code> CLI.
          </>
        }
        divided
      >
        {null}
      </Section>
    );
  }

  const selectedCount = Object.values(selected).filter(Boolean).length;

  return (
    <Section
      title="Bootstrap recipe"
      description={
        <>
          Driver recipe from{" "}
          <Badge variant="outline" className="text-2xs mx-1 font-mono">
            {plan.providerPluginSlug || "unknown"}
          </Badge>
          : pre-tuned helm values per component. Re-installing converges via Flux; un-checking a
          previously-installed component deletes its HelmRelease on the next install.
        </>
      }
      divided
    >
      <div className="flex min-w-0 flex-col gap-4">
        {plan.components.map((c) => (
          <div key={c.key} className="min-w-0 rounded-md border p-3">
            <label className="flex cursor-pointer items-start gap-3 text-sm">
              <input
                type="checkbox"
                checked={!!selected[c.key]}
                onChange={(e) =>
                  setDraft((s) => ({
                    ...s,
                    selected: { ...s.selected, [c.key]: e.target.checked },
                  }))
                }
                className="mt-1 size-4 cursor-pointer"
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2 font-medium">
                  {c.title}
                  {c.installedByRecipe && (
                    <Badge variant="secondary" className="text-2xs">
                      installed by the recipe
                    </Badge>
                  )}
                  {c.runningOutsideRecipe && (
                    <Badge variant="outline" className="text-2xs">
                      already running outside the recipe
                    </Badge>
                  )}
                </div>
                <div className="text-muted-foreground mt-0.5 text-xs">{c.rationale}</div>
                {c.requires.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {c.requires.map((r) => (
                      <Badge key={r} variant="secondary" className="text-2xs font-mono">
                        requires: {r}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            </label>
            {selected[c.key] && c.options.length > 0 && (
              <div className="mt-3 ml-7 space-y-2">
                {c.options.map((o) => (
                  <div key={o.key} className="flex min-w-0 items-center gap-2">
                    <span className="text-muted-foreground w-32 truncate text-xs">{o.label}</span>
                    <select
                      value={optionValues[c.key]?.[o.key] ?? o.default}
                      onChange={(e) =>
                        setDraft((prev) => ({
                          ...prev,
                          options: {
                            ...prev.options,
                            [c.key]: { ...prev.options[c.key], [o.key]: e.target.value },
                          },
                        }))
                      }
                      className="border-input bg-background min-w-0 flex-1 rounded-md border px-2 py-1 text-xs"
                    >
                      {o.choices.map((ch) => (
                        <option key={ch.value} value={ch.value}>
                          {ch.label}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-t pt-3">
        <span className="text-muted-foreground font-mono text-xs">
          {selectedCount} of {plan.components.length} selected
        </span>
        <Can permission="cluster.manage">
          <Button
            size="sm"
            onClick={() => onInstall(selected, optionValues)}
            disabled={installing || selectedCount === 0}
          >
            {installing ? (
              <>
                <Loader2Icon className="size-3 animate-spin" />
                Installing…
              </>
            ) : (
              <>
                <PlayIcon className="size-3" />
                Install / reconcile
              </>
            )}
          </Button>
        </Can>
      </div>
    </Section>
  );
}
