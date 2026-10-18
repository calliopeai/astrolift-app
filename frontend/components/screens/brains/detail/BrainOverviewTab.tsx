"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  ExternalLinkIcon,
  GitCommitHorizontalIcon,
  GlobeIcon,
  SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { DefinitionList } from "@/components/ui/definition-list";
import { useRelativeAge } from "@/lib/format";

import { freshnessOf, healthOf, INGEST_TONE, mcpUrl, shortSha } from "../brain-status-model";
import type { BrainStatusResult } from "../brain-status-model";
import type { FramedBrain } from "./brain-detail-model";
import { type EnvironmentChoice, EnvironmentPicker } from "./EnvironmentPicker";

export interface BrainOverviewTabProps {
  brain: FramedBrain;
  /** The environment the status is read in (`?env=`). */
  environment: string;
  /** Every environment the layer can be read in; a picker shows when there are two or more. */
  environments: EnvironmentChoice[];
  status: BrainStatusResult;
  onRetry: () => void;
}

/**
 * Overview (#2427): in one environment, the latest deployment's health, the
 * running revision against the branch head, the last compile and ingest,
 * and every endpoint the layer is served at. The status read goes to the
 * source host, so its failed reads are listed first; the endpoints come
 * with the layer and show while the status is still on its way. Pure.
 */
export function BrainOverviewTab({
  brain,
  environment,
  environments,
  status,
  onRetry,
}: BrainOverviewTabProps) {
  const t = useTranslations("brains.overview");
  const statusText = useTranslations("brains.status");
  const age = useRelativeAge();
  const loading = status.state === "loading";
  const error = status.state === "error" ? status.message : null;
  const read = status.state === "ready" ? status.status : null;
  const missing = status.state === "ready" && !read;
  const health = healthOf(read);
  const fresh = read ? freshnessOf(read) : null;
  const deployment = read?.deployment ?? null;
  const failed = deployment?.status === "failed";

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <EnvironmentPicker environments={environments} />

      {fresh && fresh.warnings.length > 0 && (
        <div
          role="status"
          className="border-warning-border bg-warning/10 flex min-w-0 items-start gap-2 rounded-md border px-3 py-2"
        >
          <AlertTriangleIcon className="text-warning mt-0.5 size-4 shrink-0" aria-hidden />
          <div className="min-w-0">
            <p className="text-warning-fg text-sm font-medium">
              {statusText("warnings", { count: fresh.warnings.length })}
            </p>
            <ul className="text-muted-foreground mt-1 space-y-0.5 font-mono text-xs [overflow-wrap:anywhere]">
              {fresh.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <PanelGrid>
        <Panel
          title={t("health")}
          icon={<ActivityIcon className="size-4" />}
          span={6}
          loading={loading}
          error={error}
          onRetry={onRetry}
          empty={
            missing
              ? {
                  icon: <GlobeIcon className="size-5" />,
                  title: t("noEnvironment", { environment }),
                }
              : health.kind === "notDeployed"
                ? {
                    icon: <GlobeIcon className="size-5" />,
                    title: t("notDeployed"),
                    description: t("notDeployedDescription"),
                  }
                : null
          }
          failure={
            failed && deployment
              ? {
                  title: t("deployFailed"),
                  reason: t("deployFailedReason"),
                  action: (
                    <Link
                      href={`/deployments/${deployment.id}`}
                      className="text-primary text-xs font-medium hover:underline"
                    >
                      {t("openDeployment")}
                    </Link>
                  ),
                }
              : null
          }
        >
          {read && deployment && (
            <DefinitionList
              items={[
                {
                  term: t("latestDeploy"),
                  description:
                    health.kind === "deployment" ? (
                      <DeploymentStatusPill status={health.status} />
                    ) : (
                      <Badge variant="outline" className="text-muted-foreground font-mono">
                        {deployment.status}
                      </Badge>
                    ),
                },
                {
                  term: t("started"),
                  description: deployment.createdAt ? (
                    <span title={deployment.createdAt}>{age(deployment.createdAt)}</span>
                  ) : (
                    t("unknown")
                  ),
                },
                {
                  term: t("image"),
                  description: deployment.imageTag ? (
                    <span className="font-mono">{deployment.imageTag}</span>
                  ) : (
                    <span className="text-muted-foreground">{t("noImage")}</span>
                  ),
                },
                {
                  term: t("deployment"),
                  description: (
                    <Link
                      href={`/deployments/${deployment.id}`}
                      className="text-primary font-mono hover:underline"
                    >
                      {deployment.id.slice(0, 8)}
                    </Link>
                  ),
                },
                {
                  term: t("served"),
                  description: read.served ? (
                    <span className="inline-flex items-center gap-1.5">
                      <StatusDot status="ok" />
                      {t("yes")}
                    </span>
                  ) : (
                    <span className="text-muted-foreground inline-flex items-center gap-1.5">
                      <StatusDot status="muted" />
                      {t("notServed")}
                    </span>
                  ),
                },
              ]}
            />
          )}
        </Panel>

        <Panel
          title={t("revision")}
          icon={<GitCommitHorizontalIcon className="size-4" />}
          span={6}
          loading={loading}
          error={error}
          onRetry={onRetry}
          empty={
            missing
              ? {
                  icon: <GitCommitHorizontalIcon className="size-5" />,
                  title: t("noEnvironment", { environment }),
                }
              : null
          }
          actions={
            fresh?.behind === true ? (
              <Badge
                variant="outline"
                className="border-warning-border bg-warning/10 text-warning-fg"
              >
                {statusText("behind")}
              </Badge>
            ) : fresh?.behind === false ? (
              <Badge
                variant="outline"
                className="border-success-border bg-success/10 text-success-fg"
              >
                {t("atHead")}
              </Badge>
            ) : undefined
          }
        >
          {read && (
            <DefinitionList
              items={[
                {
                  term: t("running"),
                  description: read.deployedCommit ? (
                    <span className="font-mono" title={read.deployedCommit}>
                      {shortSha(read.deployedCommit)}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">{t("nothingRunning")}</span>
                  ),
                },
                {
                  term: t("head"),
                  description: read.sourceHead ? (
                    <span className="font-mono">
                      <span title={read.sourceHead.commitSha}>
                        {shortSha(read.sourceHead.commitSha)}
                      </span>{" "}
                      <span className="text-muted-foreground">
                        {t("onBranch", { branch: read.sourceHead.branch })}
                      </span>
                    </span>
                  ) : (
                    <span className="text-muted-foreground">{t("headUnknown")}</span>
                  ),
                },
                {
                  term: t("committed"),
                  description: read.sourceHead?.committedAt ? (
                    <span title={read.sourceHead.committedAt}>
                      {age(read.sourceHead.committedAt)}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">{t("unknown")}</span>
                  ),
                },
              ]}
            />
          )}
        </Panel>

        <Panel
          title={t("freshness")}
          icon={<SparklesIcon className="size-4" />}
          span={6}
          loading={loading}
          error={error}
          onRetry={onRetry}
          empty={
            missing
              ? {
                  icon: <SparklesIcon className="size-5" />,
                  title: t("noEnvironment", { environment }),
                }
              : null
          }
        >
          {read && fresh && (
            <DefinitionList
              items={[
                {
                  term: t("compiled"),
                  description: fresh.compiledAt ? (
                    <span title={fresh.compiledAt}>{age(fresh.compiledAt)}</span>
                  ) : read.deployedCommit ? (
                    <span className="text-warning-fg">{t("notCompiled")}</span>
                  ) : (
                    <span className="text-muted-foreground">{t("nothingRunning")}</span>
                  ),
                },
                {
                  term: t("lastIngest"),
                  description: fresh.ingest ? (
                    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="inline-flex items-center gap-1.5">
                        <StatusDot
                          status={
                            fresh.ingest.state === "running"
                              ? "pending"
                              : INGEST_TONE[fresh.ingest.state] === "ok"
                                ? "ok"
                                : INGEST_TONE[fresh.ingest.state] === "error"
                                  ? "error"
                                  : "muted"
                          }
                        />
                        {statusText(`ingest.${fresh.ingest.state}`)}
                      </span>
                      <span className="text-muted-foreground" title={fresh.ingest.at}>
                        {age(fresh.ingest.at)}
                      </span>
                      {fresh.ingest.url && (
                        <a
                          href={fresh.ingest.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-primary inline-flex items-center gap-0.5 hover:underline"
                        >
                          {t("openRun")}
                          <ExternalLinkIcon className="size-3" aria-hidden />
                        </a>
                      )}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">{t("noIngest")}</span>
                  ),
                },
              ]}
            />
          )}
        </Panel>

        <Panel
          title={t("endpoints")}
          icon={<GlobeIcon className="size-4" />}
          span={6}
          empty={
            brain.environments.length === 0
              ? {
                  icon: <GlobeIcon className="size-5" />,
                  title: t("notServed"),
                  description: t("notServedDescription"),
                }
              : null
          }
        >
          <ul className="flex min-w-0 flex-col gap-3">
            {brain.environments.map((env) => (
              <li key={env.name} className="min-w-0">
                <p className="text-muted-foreground font-mono text-xs">{env.name}</p>
                <a
                  href={env.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block truncate font-mono text-sm hover:underline"
                  title={env.url}
                >
                  {env.url}
                </a>
                <p
                  className="text-muted-foreground truncate font-mono text-xs"
                  title={mcpUrl(env.url)}
                >
                  {t("mcp", { url: mcpUrl(env.url) })}
                </p>
              </li>
            ))}
          </ul>
        </Panel>
      </PanelGrid>
    </div>
  );
}
