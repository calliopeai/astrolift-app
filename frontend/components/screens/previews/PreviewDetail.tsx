"use client";

import {
  AlertTriangleIcon,
  CpuIcon,
  ExternalLinkIcon,
  GitPullRequestIcon,
  InfoIcon,
  MoreHorizontalIcon,
} from "lucide-react";
import Link from "next/link";

import { DetailTimestamp } from "@/components/detail/EntityDetailShell";
import { Identifier } from "@/components/Identifier";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { appsDetailCrumbs } from "@/components/screens/deployments/apps-area";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import { PREVIEW_DOT } from "./previews-list";
import type { usePreviewDetail } from "./use-preview-detail";

function formatMemory(bytes: number): string {
  if (!bytes) return "0 MiB";
  const gib = bytes / (1024 * 1024 * 1024);
  if (gib >= 1) return `${gib.toFixed(2)} GiB`;
  return `${Math.round(bytes / (1024 * 1024))} MiB`;
}

export type PreviewDetailScreenProps = ReturnType<typeof usePreviewDetail>;

/** Previews ▾ › checkout-api › PR #412 (spec 44 §4.4). */
function crumbs(id: string, p: AstroliftPreviewEnvironment | null) {
  if (!p) return appsDetailCrumbs("previews", { label: `preview ${id.slice(0, 8)}` });
  return appsDetailCrumbs(
    "previews",
    { label: p.registeredAppSlug, href: `/apps/${p.registeredAppSlug}/deployments?view=previews` },
    { label: `PR #${p.prNumber}` }
  );
}

/**
 * One preview environment on the detail language (spec 44 §5.2, #1106): its
 * status and branch in the header, Open preview as the action while it
 * runs, a failure first, then its fields and resources in panels. Pure
 * view; the data half is usePreviewDetail.
 */
export function PreviewDetailScreen({
  id,
  preview: p,
  loading,
  error,
  onRetry,
}: PreviewDetailScreenProps) {
  if (!p) {
    const title = loading || error ? "Preview" : "Preview not found";
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <ShellHeader crumbs={crumbs(id, null)} title={title} />
        <PanelGrid>
          <Panel
            title="Preview"
            icon={<GitPullRequestIcon className="size-4" />}
            span={6}
            loading={loading}
            error={error}
            onRetry={onRetry}
            empty={{
              icon: <GitPullRequestIcon className="size-5" />,
              title: "Preview not found",
              description:
                "No preview environment has this id in the recent window, or you do not have permission to see it.",
              actionHref: "/previews",
              actionLabel: "Open previews",
            }}
          />
          {loading && (
            <Panel title="Resources" icon={<CpuIcon className="size-4" />} span={6} loading />
          )}
        </PanelGrid>
      </div>
    );
  }

  const running = p.status === "running";

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader
        crumbs={crumbs(id, p)}
        title={`PR #${p.prNumber}`}
        status={
          <span className="inline-flex shrink-0 items-center gap-1.5 text-sm capitalize">
            <StatusDot status={PREVIEW_DOT[p.status]} />
            {p.status.replace(/_/g, " ")}
          </span>
        }
        context={
          <>
            <span className="font-mono">{p.branch}</span>
            {p.commitSha && (
              <>
                {" · "}
                <span className="font-mono">{p.commitSha.slice(0, 8)}</span>
              </>
            )}
          </>
        }
        primaryAction={
          running ? (
            <Button size="sm" variant="outline" asChild>
              <a href={`https://${p.hostname}`} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-4" />
                Open preview
              </a>
            </Button>
          ) : undefined
        }
        menu={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon" className="size-8" aria-label="More actions">
                <MoreHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-44">
              {p.prUrl && (
                <DropdownMenuItem asChild>
                  <a href={p.prUrl} target="_blank" rel="noreferrer">
                    Open pull request
                  </a>
                </DropdownMenuItem>
              )}
              {p.sourceUrl && (
                <DropdownMenuItem asChild>
                  <a href={p.sourceUrl} target="_blank" rel="noreferrer">
                    Open repository
                  </a>
                </DropdownMenuItem>
              )}
              <DropdownMenuItem asChild>
                <Link href={`/apps/${p.registeredAppSlug}`}>Open app</Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />

      <PanelGrid className="items-start">
        <Panel
          title="Overview"
          icon={<InfoIcon className="size-4" />}
          span={6}
          // No reason field on a preview yet: say where to look.
          failure={
            p.status === "failed"
              ? {
                  title: "Preview failed",
                  reason: "The preview did not build or deploy. Its app's logs hold the reason.",
                  action: (
                    <Button size="sm" variant="outline" asChild>
                      <Link href={`/apps/${p.registeredAppSlug}/logs`}>Open logs</Link>
                    </Button>
                  ),
                }
              : null
          }
        >
          <DefinitionList
            items={[
              {
                term: "App",
                description: (
                  <Link href={`/apps/${p.registeredAppSlug}`} className="hover:underline">
                    {p.registeredAppSlug}
                  </Link>
                ),
              },
              {
                term: "Hostname",
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">{p.hostname}</span>
                ),
              },
              {
                term: "Namespace",
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">{p.namespace}</span>
                ),
              },
              {
                term: "Commit",
                description: p.commitSha ? (
                  <Identifier value={p.commitSha} kind="sha" form="full" />
                ) : (
                  "—"
                ),
              },
              { term: "Trigger", description: p.isManual ? "Manual" : "Automatic" },
              {
                term: "Pinned",
                description: p.isPinned ? (
                  <span className="[overflow-wrap:anywhere]">
                    {p.pinnedByEmail ?? "yes"}
                    {p.pinReason && ` · ${p.pinReason}`}
                  </span>
                ) : (
                  "No"
                ),
              },
              { term: "TTL until", description: <DetailTimestamp iso={p.ttlUntil} /> },
              { term: "Last deployed", description: <DetailTimestamp iso={p.lastDeployedAt} /> },
              { term: "Torn down", description: <DetailTimestamp iso={p.tornDownAt} /> },
            ]}
          />
        </Panel>

        <Panel title="Resources" icon={<CpuIcon className="size-4" />} span={6}>
          <DefinitionList
            items={[
              {
                term: "vCPU (aggregate)",
                description: (
                  <span className="font-mono">{p.aggregateResources.cpuCores.toFixed(2)}</span>
                ),
              },
              {
                term: "Memory (aggregate)",
                description: (
                  <span className="font-mono">
                    {formatMemory(p.aggregateResources.memoryBytes)}
                  </span>
                ),
              },
              {
                term: "Pods",
                description: <span className="font-mono">{p.aggregateResources.podCount}</span>,
              },
              {
                term: "Est. daily cost",
                // The figure and what the driver said about it (#1509): a
                // GCP variant's total is an over-count by construction, and
                // an operator must not read it as exact.
                description:
                  p.estimatedDailyCostUsd == null ? (
                    "—"
                  ) : (
                    <span className="flex min-w-0 flex-col gap-1">
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono">{`$${p.estimatedDailyCostUsd.toFixed(2)}`}</span>
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
                            <span key={note} className="[overflow-wrap:anywhere]">
                              {note}
                            </span>
                          ))}
                        </span>
                      )}
                    </span>
                  ),
              },
            ]}
          />
        </Panel>
      </PanelGrid>
    </div>
  );
}
