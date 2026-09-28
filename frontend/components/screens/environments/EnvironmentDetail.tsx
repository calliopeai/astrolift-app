"use client";

import {
  CloudIcon,
  ExternalLinkIcon,
  InfoIcon,
  MoreHorizontalIcon,
  SlidersHorizontalIcon,
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
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { EnvironmentState } from "./use-environment";

export type EnvironmentDetailProps = EnvironmentState & { id: string };

function PausedBadge({ paused }: { paused: boolean }) {
  return paused ? (
    <Badge variant="destructive">Paused</Badge>
  ) : (
    <Badge variant="secondary">Active</Badge>
  );
}

/** Environments ▾ › storefront › prod (spec 44 §4.4). */
function crumbs(id: string, e: AstroliftAppEnvironment | null) {
  if (!e) return appsDetailCrumbs("environments", { label: `environment ${id.slice(0, 8)}` });
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
  if (!e) {
    const title = loading || error ? "Environment" : "Environment not found";
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <ShellHeader crumbs={crumbs(id, null)} title={title} />
        <PanelGrid>
          <Panel
            title="Environment"
            icon={<CloudIcon className="size-4" />}
            span={6}
            loading={loading}
            error={error}
            onRetry={onRetry}
            empty={{
              icon: <CloudIcon className="size-5" />,
              title: "Environment not found",
              description: "No environment has this id, or you do not have permission to see it.",
              actionHref: "/environments",
              actionLabel: "Open environments",
            }}
          />
          {loading && (
            <Panel
              title="Settings"
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
        crumbs={crumbs(id, e)}
        title={<span className="font-mono">{e.name}</span>}
        status={
          <span className="inline-flex shrink-0 items-center gap-1.5 text-sm">
            <StatusDot status={e.deploysPaused ? "warn" : "ok"} />
            {e.deploysPaused ? "Deploys paused" : "Deploys active"}
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
                Open
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
              <DropdownMenuItem asChild>
                <Link href={`/apps/${e.registeredAppSlug}/settings?section=environments`}>
                  Environment settings
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link
                  href={`/deployments?app=${encodeURIComponent(e.registeredAppSlug)}&environment=${encodeURIComponent(e.name)}`}
                >
                  Deployments here
                </Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />

      <PanelGrid className="items-start">
        <Panel title="Overview" icon={<InfoIcon className="size-4" />} span={6}>
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
                term: "Provider",
                description: (
                  <span className="font-mono text-xs">{e.clusterProviderPluginSlug ?? "—"}</span>
                ),
              },
              {
                term: "Domain zone",
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {e.domainZone ?? "—"}
                  </span>
                ),
              },
              {
                term: "Required approvals",
                description: <span className="font-mono">{e.requiredApprovals}</span>,
              },
              { term: "Deploys", description: <PausedBadge paused={e.deploysPaused} /> },
              { term: "Ingress", description: <PausedBadge paused={e.ingressPaused} /> },
              { term: "Created", description: <DetailTimestamp iso={e.createdAt} /> },
            ]}
          />
        </Panel>

        <Panel
          title="Settings"
          icon={<SlidersHorizontalIcon className="size-4" />}
          span={6}
          actions={
            <span className="text-muted-foreground font-mono text-xs">{e.settings.length}</span>
          }
          empty={
            e.settings.length === 0
              ? {
                  icon: <SlidersHorizontalIcon className="size-5" />,
                  title: "No environment settings",
                  description: "This environment has no per-environment settings configured.",
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
