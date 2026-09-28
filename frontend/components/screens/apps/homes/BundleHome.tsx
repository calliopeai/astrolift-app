"use client";

import { BoxIcon, ChevronRightIcon, LayersIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Panel, PanelGrid } from "@/components/panel/Panel";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { WORKLOAD_KIND_META } from "@/lib/primitive";

export interface BundleHomeScreenProps {
  /** The app's name; the frame above shows it. */
  name: string;
  /** The app's provisioning status; the frame above shows it. */
  status: string;
  workloads: AstroliftWorkload[];
  /** Detail URL for one of the bundle's workloads (the route knows the base path). */
  workloadHref: (workloadSlug: string) => string;
}

/**
 * The Overview for a **bundle**: an app that packages several workloads of
 * different kinds. One panel of hairline rows, each wearing its own
 * primitive icon, so the composition reads at a glance; each row opens that
 * workload's detail.
 */
export function BundleHomeScreen({ workloads, workloadHref }: BundleHomeScreenProps) {
  const kinds = React.useMemo(() => Array.from(new Set(workloads.map((w) => w.kind))), [workloads]);

  return (
    <PanelGrid>
      <Panel
        title="Workloads in this bundle"
        icon={<LayersIcon className="size-4" />}
        description={`${workloads.length} workload${workloads.length === 1 ? "" : "s"} across ${kinds.length} kind${kinds.length === 1 ? "" : "s"}. Open any workload for its deploy, config and logs.`}
        flush
        empty={
          workloads.length === 0
            ? {
                icon: <BoxIcon className="size-5" />,
                title: "No workloads yet",
                description: "Workloads appear here once the manifest registers them.",
              }
            : null
        }
      >
        <ul className="divide-y">
          {workloads.map((w) => {
            const meta = WORKLOAD_KIND_META[w.kind] ?? WORKLOAD_KIND_META.deployment;
            const Icon = meta.Icon;
            return (
              <li key={w.id}>
                <Link
                  href={workloadHref(w.slug)}
                  className="group hover:bg-muted/40 focus-visible:ring-ring/50 flex min-w-0 items-center gap-3 px-4 py-2.5 outline-none focus-visible:ring-3"
                >
                  <Icon aria-hidden className="text-muted-foreground size-4 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block font-mono text-sm [overflow-wrap:anywhere]">
                      {w.name}
                    </span>
                    <span className="text-muted-foreground flex flex-wrap gap-x-3 text-xs">
                      <span>{meta.label}</span>
                      {w.kind === "cronjob" && w.schedule ? (
                        <span className="font-mono">{w.schedule}</span>
                      ) : null}
                      {(w.kind === "deployment" || w.kind === "statefulset") &&
                      w.replicas != null ? (
                        <span>
                          <span className="font-mono">{w.replicas}</span> replica
                          {w.replicas === 1 ? "" : "s"}
                        </span>
                      ) : null}
                      {w.isPublic ? <span className="text-info-fg">public</span> : null}
                    </span>
                  </span>
                  <ChevronRightIcon
                    aria-hidden
                    className="text-muted-foreground size-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                  />
                </Link>
              </li>
            );
          })}
        </ul>
      </Panel>
    </PanelGrid>
  );
}
