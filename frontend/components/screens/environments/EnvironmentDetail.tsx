"use client";

import {
  CloudIcon,
  ExternalLinkIcon,
  InfoIcon,
  MoreHorizontalIcon,
  SlidersHorizontalIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

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
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { EnvironmentState } from "./use-environment";

export type EnvironmentDetailProps = EnvironmentState & { id: string };

function PausedBadge({ paused }: { paused: boolean }) {
  const t = useTranslations("lists.environments");
  return paused ? (
    <Badge variant="destructive">{t("paused")}</Badge>
  ) : (
    <Badge variant="secondary">{t("active")}</Badge>
  );
}

/** Environments ▾ › storefront › prod (spec 44 §4.4). */
function crumbs(e: AstroliftAppEnvironment | null, fallback: string) {
  if (!e) return appsDetailCrumbs("environments", { label: fallback });
  return appsDetailCrumbs(
    "environments",
    {
      label: e.registeredAppSlug,
      href: `/apps/${e.registeredAppSlug}/settings?section=environments`,
    },
    { label: e.name }
  );
}

/**
 * One environment on the detail language (spec 44 §5.2): the header says
 * whether deploys run, its app and cluster are links, and its fields and
 * per-environment settings sit in panels. Pure view; the data half is
 * useEnvironment.
 */
export function EnvironmentDetail({
  id,
  loading,
  environment: e,
  error,
  onRetry,
}: EnvironmentDetailProps) {
  const t = useTranslations("lists.environments.detail");
  const fallback = t("environmentLabel", { id: id.slice(0, 8) });
  if (!e) {
    const title = loading || error ? t("title") : t("notFound");
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <ShellHeader crumbs={crumbs(null, fallback)} title={title} />
        <PanelGrid>
          <Panel
            title={t("title")}
            icon={<CloudIcon className="size-4" />}
            span={6}
            loading={loading}
            error={error}
            onRetry={onRetry}
            empty={{
              icon: <CloudIcon className="size-5" />,
              title: t("notFound"),
              description: t("notFoundDescription"),
              actionHref: "/environments",
              actionLabel: t("openEnvironments"),
            }}
          />
          {loading && (
            <Panel
              title={t("settings")}
              icon={<SlidersHorizontalIcon className="size-4" />}
              span={6}
              loading
            />
          )}
        </PanelGrid>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader
        crumbs={crumbs(e, fallback)}
        title={<span className="font-mono">{e.name}</span>}
        status={
          <span className="inline-flex shrink-0 items-center gap-1.5 text-sm">
            <StatusDot status={e.deploysPaused ? "warn" : "ok"} />
            {e.deploysPaused ? t("deploysPaused") : t("deploysActive")}
          </span>
        }
        context={
          <>
            <Link href={`/apps/${e.registeredAppSlug}`} className="hover:underline">
              {e.registeredAppSlug}
            </Link>
            {e.clusterSlug && (
              <>
                {" · "}
                <Link href={`/clusters/${e.clusterSlug}`} className="font-mono hover:underline">
                  {e.clusterSlug}
                </Link>
              </>
            )}
          </>
        }
        primaryAction={
          e.url ? (
            <Button size="sm" variant="outline" asChild>
              <a href={e.url} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-4" />
                {t("open")}
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
                aria-label={t("moreActions")}
              >
                <MoreHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-44">
              <DropdownMenuItem asChild>
                <Link href={`/apps/${e.registeredAppSlug}/settings?section=environments`}>
                  {t("environmentSettings")}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link
                  href={`/deployments?app=${encodeURIComponent(e.registeredAppSlug)}&environment=${encodeURIComponent(e.name)}`}
                >
                  {t("deploymentsHere")}
                </Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />

      <PanelGrid className="items-start">
        <Panel title={t("overview")} icon={<InfoIcon className="size-4" />} span={6}>
          <DefinitionList
            items={[
              { term: "ID", description: <Identifier value={e.id} form="full" /> },
              {
                term: "URL",
                description: e.url ? (
                  <a
                    href={e.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sm [overflow-wrap:anywhere] hover:underline"
                  >
                    {e.url}
                  </a>
                ) : (
                  "—"
                ),
              },
              {
                term: t("provider"),
                description: (
                  <span className="font-mono text-xs">{e.clusterProviderPluginSlug ?? "—"}</span>
                ),
              },
              {
                term: t("domainZone"),
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {e.domainZone ?? "—"}
                  </span>
                ),
              },
              {
                term: t("requiredApprovals"),
                description: <span className="font-mono">{e.requiredApprovals}</span>,
              },
              { term: t("deploys"), description: <PausedBadge paused={e.deploysPaused} /> },
              { term: t("ingress"), description: <PausedBadge paused={e.ingressPaused} /> },
              { term: t("created"), description: <DetailTimestamp iso={e.createdAt} /> },
            ]}
          />
        </Panel>

        <Panel
          title={t("settings")}
          icon={<SlidersHorizontalIcon className="size-4" />}
          span={6}
          actions={
            <span className="text-muted-foreground font-mono text-xs">{e.settings.length}</span>
          }
          empty={
            e.settings.length === 0
              ? {
                  icon: <SlidersHorizontalIcon className="size-5" />,
                  title: t("noSettings"),
                  description: t("noSettingsDescription"),
                }
              : null
          }
        >
          <dl className="divide-border divide-y text-sm">
            {e.settings.map((s) => (
              <div
                key={s.id}
                className="grid grid-cols-[minmax(0,14rem)_1fr] items-baseline gap-4 py-2 first:pt-0 last:pb-0"
              >
                <dt className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
                  {s.key}
                </dt>
                <dd className="text-foreground min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                  {s.value}
                </dd>
              </div>
            ))}
          </dl>
        </Panel>
      </PanelGrid>
    </div>
  );
}
