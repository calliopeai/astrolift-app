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
import { useFormatter, useTranslations } from "next-intl";

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

function formatMemory(
  bytes: number,
  formatNumber: (value: number, places: number) => string
): string {
  if (!bytes) return `${formatNumber(0, 0)} MiB`;
  const gib = bytes / (1024 * 1024 * 1024);
  if (gib >= 1) return `${formatNumber(gib, 2)} GiB`;
  return `${formatNumber(Math.round(bytes / (1024 * 1024)), 0)} MiB`;
}

export type PreviewDetailScreenProps = ReturnType<typeof usePreviewDetail>;

/** Previews ▾ › checkout-api › PR #412 (spec 44 §4.4). */
function crumbs(p: AstroliftPreviewEnvironment | null, fallback: string) {
  if (!p) return appsDetailCrumbs("previews", { label: fallback });
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
  const t = useTranslations("lists.previews");
  const fmt = useFormatter();
  const fallback = t("detail.previewLabel", { id: id.slice(0, 8) });
  const formatNumber = (value: number, places: number) =>
    fmt.number(value, { minimumFractionDigits: places, maximumFractionDigits: places });
  if (!p) {
    const title = loading || error ? t("detail.title") : t("detail.notFound");
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <ShellHeader crumbs={crumbs(null, fallback)} title={title} />
        <PanelGrid>
          <Panel
            title={t("detail.title")}
            icon={<GitPullRequestIcon className="size-4" />}
            span={6}
            loading={loading}
            error={error}
            onRetry={onRetry}
            empty={{
              icon: <GitPullRequestIcon className="size-5" />,
              title: t("detail.notFound"),
              description: t("detail.notFoundDescription"),
              actionHref: "/previews",
              actionLabel: t("detail.openPreviews"),
            }}
          />
          {loading && (
            <Panel
              title={t("detail.resources")}
              icon={<CpuIcon className="size-4" />}
              span={6}
              loading
            />
          )}
        </PanelGrid>
      </div>
    );
  }

  const running = p.status === "running";

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader
        crumbs={crumbs(p, fallback)}
        title={`PR #${p.prNumber}`}
        status={
          <span className="inline-flex shrink-0 items-center gap-1.5 text-sm capitalize">
            <StatusDot status={PREVIEW_DOT[p.status]} />
            {t(`status.${p.status}`)}
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
                {t("openPreview")}
              </a>
            </Button>
          ) : undefined
        }
        menu={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                size="icon"
                className="size-8"
                aria-label={t("detail.moreActions")}
              >
                <MoreHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-44">
              {p.prUrl && (
                <DropdownMenuItem asChild>
                  <a href={p.prUrl} target="_blank" rel="noreferrer">
                    {t("openPullRequest")}
                  </a>
                </DropdownMenuItem>
              )}
              {p.sourceUrl && (
                <DropdownMenuItem asChild>
                  <a href={p.sourceUrl} target="_blank" rel="noreferrer">
                    {t("openRepository")}
                  </a>
                </DropdownMenuItem>
              )}
              <DropdownMenuItem asChild>
                <Link href={`/apps/${p.registeredAppSlug}`}>{t("openApp")}</Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />

      <PanelGrid className="items-start">
        <Panel
          title={t("detail.overview")}
          icon={<InfoIcon className="size-4" />}
          span={6}
          // No reason field on a preview yet: say where to look.
          failure={
            p.status === "failed"
              ? {
                  title: t("detail.failed"),
                  reason: t("detail.failureReason"),
                  action: (
                    <Button size="sm" variant="outline" asChild>
                      <Link href={`/apps/${p.registeredAppSlug}/logs`}>{t("detail.openLogs")}</Link>
                    </Button>
                  ),
                }
              : null
          }
        >
          <DefinitionList
            items={[
              {
                term: t("columns.app"),
                description: (
                  <Link href={`/apps/${p.registeredAppSlug}`} className="hover:underline">
                    {p.registeredAppSlug}
                  </Link>
                ),
              },
              {
                term: t("columns.hostname"),
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">{p.hostname}</span>
                ),
              },
              {
                term: t("detail.namespace"),
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">{p.namespace}</span>
                ),
              },
              {
                term: t("detail.commit"),
                description: p.commitSha ? (
                  <Identifier value={p.commitSha} kind="sha" form="full" />
                ) : (
                  "—"
                ),
              },
              {
                term: t("detail.trigger"),
                description: p.isManual ? t("detail.manual") : t("detail.automatic"),
              },
              {
                term: t("detail.pinned"),
                description: p.isPinned ? (
                  <span className="[overflow-wrap:anywhere]">
                    {p.pinnedByEmail ?? t("detail.yes")}
                    {p.pinReason && ` · ${p.pinReason}`}
                  </span>
                ) : (
                  t("detail.no")
                ),
              },
              { term: t("detail.ttlUntil"), description: <DetailTimestamp iso={p.ttlUntil} /> },
              {
                term: t("detail.lastDeployed"),
                description: <DetailTimestamp iso={p.lastDeployedAt} />,
              },
              { term: t("detail.tornDown"), description: <DetailTimestamp iso={p.tornDownAt} /> },
            ]}
          />
        </Panel>

        <Panel title={t("detail.resources")} icon={<CpuIcon className="size-4" />} span={6}>
          <DefinitionList
            items={[
              {
                term: t("detail.cpu"),
                description: (
                  <span className="font-mono">
                    {formatNumber(p.aggregateResources.cpuCores, 2)}
                  </span>
                ),
              },
              {
                term: t("detail.memory"),
                description: (
                  <span className="font-mono">
                    {formatMemory(p.aggregateResources.memoryBytes, formatNumber)}
                  </span>
                ),
              },
              {
                term: t("detail.pods"),
                description: <span className="font-mono">{p.aggregateResources.podCount}</span>,
              },
              {
                term: t("detail.dailyCost"),
                // The figure and what the driver said about it (#1509): a
                // GCP variant's total is an over-count by construction, and
                // an operator must not read it as exact.
                description:
                  p.estimatedDailyCostUsd == null ? (
                    "—"
                  ) : (
                    <span className="flex min-w-0 flex-col gap-1">
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono">
                          {fmt.number(p.estimatedDailyCostUsd, {
                            style: "currency",
                            currency: "USD",
                          })}
                        </span>
                        {p.estimatedCostApproximate && (
                          <Badge variant="outline" className="text-2xs gap-1 uppercase">
                            <AlertTriangleIcon className="size-3" />
                            {t("detail.approximate")}
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
