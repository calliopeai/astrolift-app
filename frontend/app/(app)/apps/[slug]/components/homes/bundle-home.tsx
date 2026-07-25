"use client";

import { BoxIcon, LayersIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { AppTabs } from "../app-tabs";
import { appPath, useAppChrome } from "../app-chrome-context";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { WORKLOAD_KIND_META } from "@/lib/primitive";

interface BundleHomeProps {
  slug: string;
  name: string;
  status: string;
  workloads: AstroliftWorkload[];
}

/**
 * Primitive home for a **bundle** — an app that packages several workloads of
 * different kinds. Reads as "many in one": a grid of workload cards, each
 * wearing its own primitive icon, so the composition is obvious at a glance.
 * Each card deep-links to that workload's detail.
 */
export function BundleHome({ slug, name, status, workloads }: BundleHomeProps) {
  const chrome = useAppChrome();
  const kinds = React.useMemo(
    () => Array.from(new Set(workloads.map((w) => w.kind))),
    [workloads]
  );

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <BoxIcon className="text-muted-foreground size-5" />
          </span>
          <span>{name}</span>
          <Badge variant="outline" className="gap-1.5">
            <BoxIcon className="size-3" />
            Bundle
          </Badge>
        </span>
      }
      description={
        <span className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">
            {workloads.length} workload{workloads.length === 1 ? "" : "s"} across{" "}
            {kinds.length} kind{kinds.length === 1 ? "" : "s"}
          </span>
        </span>
      }
    >
      <AppTabs slug={slug} active="overview" />
      <div className="space-y-3">
        <div className="text-muted-foreground flex items-center gap-2 text-sm">
          <LayersIcon className="size-4" />
          <span>Workloads in this bundle</span>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {workloads.map((w) => {
            const meta = WORKLOAD_KIND_META[w.kind] ?? WORKLOAD_KIND_META.deployment;
            const Icon = meta.Icon;
            return (
              <Link
                key={w.id}
                href={appPath(chrome, slug, "workloads", encodeURIComponent(w.slug))}
                className="group"
              >
                <Card className="hover:ring-foreground/20 h-full transition-shadow group-hover:shadow-sm">
                  <CardContent className="flex flex-col gap-2 p-4">
                    <div className="flex items-center gap-2">
                      <span className="bg-muted flex size-8 items-center justify-center rounded-md">
                        <Icon className="text-muted-foreground size-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{w.name}</div>
                        <div className="text-muted-foreground text-xs">{meta.label}</div>
                      </div>
                    </div>
                    <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      {w.kind === "cronjob" && w.schedule ? (
                        <span className="font-mono">{w.schedule}</span>
                      ) : null}
                      {(w.kind === "deployment" || w.kind === "statefulset") &&
                      w.replicas != null ? (
                        <span>
                          {w.replicas} replica{w.replicas === 1 ? "" : "s"}
                        </span>
                      ) : null}
                      {w.isPublic ? <span className="text-info-fg">public</span> : null}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
        <p className="text-muted-foreground pt-1 text-xs">
          <StatusDot status={status === "ready" ? "ok" : status === "failed" ? "error" : "warn"} />{" "}
          App status: {status}. Open any workload for its deploy, config, and logs.
        </p>
      </div>
    </PageShell>
  );
}
