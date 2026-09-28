"use client";

import { AlertTriangleIcon, ExternalLinkIcon } from "lucide-react";
import Link from "next/link";

import {
  DetailStatusBadge,
  DetailTimestamp,
  EntityDetailShell,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import type { PreviewStatus } from "@/graphql/lifecycle/lifecycle.types";

import type { usePreviewDetail } from "./use-preview-detail";

// Preview status colour, matching the /previews list (a "running" preview is
// healthy/teal, not in-flight) so the badge reads the same across surfaces.
const STATUS_TONE: Record<PreviewStatus, Dot> = {
  building: "pending",
  running: "ok",
  failed: "error",
  torn_down: "muted",
};

function formatMemory(bytes: number): string {
  if (!bytes) return "0 MiB";
  const gib = bytes / (1024 * 1024 * 1024);
  if (gib >= 1) return `${gib.toFixed(2)} GiB`;
  return `${Math.round(bytes / (1024 * 1024))} MiB`;
}

export type PreviewDetailScreenProps = ReturnType<typeof usePreviewDetail>;

/** Preview environment detail (#1106), the drill-in target for a /previews row. */
export function PreviewDetailScreen({ id, preview: p, loading }: PreviewDetailScreenProps) {
  const tone = p ? STATUS_TONE[p.status] : undefined;

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!p}
      breadcrumb={{ label: "Previews", href: "/previews" }}
      heading={p ? `PR #${p.prNumber}` : `Preview ${id.slice(0, 8)}`}
      status={p?.status}
      statusTone={tone}
      notFoundLabel="preview environment"
      overview={
        p
          ? [
              { term: "Status", description: <DetailStatusBadge status={p.status} tone={tone} /> },
              {
                term: "App",
                description: (
                  <Link
                    href={`/apps/${p.registeredAppSlug}`}
                    className="text-[var(--brand-primary)] hover:underline"
                  >
                    {p.registeredAppSlug}
                  </Link>
                ),
              },
              {
                term: "Pull request",
                description: p.prUrl ? (
                  <a
                    href={p.prUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[var(--brand-primary)] hover:underline"
                  >
                    #{p.prNumber} <ExternalLinkIcon className="size-3" />
                  </a>
                ) : (
                  <span className="font-mono">#{p.prNumber}</span>
                ),
              },
              {
                term: "Branch",
                description: (
                  <span>
                    <span className="font-mono text-xs">{p.branch}</span>
                    {p.commitSha ? (
                      <span className="text-muted-foreground font-mono text-xs">
                        {" "}
                        · {p.commitSha.slice(0, 7)}
                      </span>
                    ) : null}
                  </span>
                ),
              },
              {
                term: "Hostname",
                description:
                  p.status === "running" ? (
                    <a
                      href={`https://${p.hostname}`}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-sm hover:underline"
                    >
                      {p.hostname} <ExternalLinkIcon className="size-3" />
                    </a>
                  ) : (
                    <span className="text-muted-foreground font-mono text-xs">{p.hostname}</span>
                  ),
              },
              {
                term: "Namespace",
                description: <span className="font-mono text-xs">{p.namespace}</span>,
              },
              {
                term: "Source",
                description: p.sourceUrl ? (
                  <a
                    href={p.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-sm text-[var(--brand-primary)] hover:underline"
                  >
                    Repository <ExternalLinkIcon className="size-3" />
                  </a>
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
              { term: "Trigger", description: p.isManual ? "Manual" : "Automatic" },
              { term: "TTL until", description: <DetailTimestamp iso={p.ttlUntil} /> },
              { term: "Last deployed", description: <DetailTimestamp iso={p.lastDeployedAt} /> },
              { term: "Torn down", description: <DetailTimestamp iso={p.tornDownAt} /> },
            ]
          : []
      }
    >
      {p ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Resources</CardTitle>
          </CardHeader>
          <CardContent>
            <DefinitionList
              items={[
                { term: "vCPU (aggregate)", description: p.aggregateResources.cpuCores.toFixed(2) },
                {
                  term: "Memory (aggregate)",
                  description: formatMemory(p.aggregateResources.memoryBytes),
                },
                { term: "Pods", description: String(p.aggregateResources.podCount) },
                {
                  term: "Est. daily cost",
                  // The figure and what the driver said about it. Every
                  // caveat used to be dropped before it reached here
                  // (#1509), including the label on GCP variants whose
                  // total is an over-count by construction — so an
                  // operator read a confident number that could be out by
                  // an order of magnitude with nothing saying so.
                  description:
                    p.estimatedDailyCostUsd == null ? (
                      "—"
                    ) : (
                      <span className="flex flex-col gap-1">
                        <span className="flex items-center gap-1.5">
                          {`$${p.estimatedDailyCostUsd.toFixed(2)}`}
                          {p.estimatedCostApproximate && (
                            <Badge variant="outline" className="text-2xs gap-1 uppercase">
                              <AlertTriangleIcon className="size-3" />
                              approximate
                            </Badge>
                          )}
                        </span>
                        {p.estimatedCostNotes.length > 0 && (
                          <span className="text-muted-foreground flex flex-col gap-0.5 text-xs">
                            {p.estimatedCostNotes.map((note) => (
                              <span key={note}>{note}</span>
                            ))}
                          </span>
                        )}
                      </span>
                    ),
                },
              ]}
            />
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
