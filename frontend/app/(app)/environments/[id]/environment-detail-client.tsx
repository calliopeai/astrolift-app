"use client";

import { useQuery } from "@apollo/client/react";
import { ExternalLinkIcon, SlidersHorizontalIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

interface Resp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

function PausedBadge({ paused }: { paused: boolean }) {
  return paused ? (
    <Badge variant="destructive">Paused</Badge>
  ) : (
    <Badge variant="secondary">Active</Badge>
  );
}

/**
 * Environment detail (#1106). Reuses the global LIST_ENVIRONMENTS query (no
 * singular query exists) — a cache hit when navigated from the global
 * /environments list. An environment has no single status axis, so pause
 * state renders as fields rather than a status badge.
 */
export function EnvironmentDetailClient({ id }: { id: string }) {
  const { data, loading } = useQuery<Resp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: null },
    fetchPolicy: "cache-and-network",
  });

  const e = React.useMemo(
    () => (data?.astroliftEnvironments ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!e}
      breadcrumb={{ label: "Environments", href: "/environments" }}
      heading={e ? `${e.registeredAppSlug} · ${e.name}` : `Environment ${id.slice(0, 8)}`}
      createdAt={e?.createdAt}
      notFoundLabel="environment"
      overview={
        e
          ? [
              { term: "Name", description: <span className="font-mono text-xs">{e.name}</span> },
              {
                term: "App",
                description: (
                  <Link
                    href={`/apps/${e.registeredAppSlug}`}
                    className="text-[var(--brand-primary)] hover:underline"
                  >
                    {e.registeredAppSlug}
                  </Link>
                ),
              },
              {
                term: "URL",
                description: e.url ? (
                  <a
                    href={e.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-sm hover:underline"
                  >
                    {e.url} <ExternalLinkIcon className="size-3" />
                  </a>
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
              {
                term: "Cluster",
                description: e.clusterSlug ? (
                  <Link
                    href={`/clusters/${e.clusterSlug}`}
                    className="text-[var(--brand-primary)] font-mono text-xs hover:underline"
                  >
                    {e.clusterSlug}
                  </Link>
                ) : (
                  <span className="text-muted-foreground">—</span>
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
                description: <span className="font-mono text-xs">{e.domainZone ?? "—"}</span>,
              },
              { term: "Required approvals", description: <span className="font-mono">{e.requiredApprovals}</span> },
              { term: "Deploys", description: <PausedBadge paused={e.deploysPaused} /> },
              { term: "Ingress", description: <PausedBadge paused={e.ingressPaused} /> },
              { term: "Created", description: <DetailTimestamp iso={e.createdAt} /> },
            ]
          : []
      }
    >
      {e ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <SlidersHorizontalIcon className="size-4" />
              Settings
              <span className="text-muted-foreground text-xs font-normal">{e.settings.length}</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {e.settings.length === 0 ? (
              <EmptyState
                icon={<SlidersHorizontalIcon className="size-5" />}
                title="No environment settings"
                description="This environment has no per-environment settings configured."
              />
            ) : (
              <dl className="divide-border divide-y text-sm">
                {e.settings.map((s) => (
                  <div
                    key={s.id}
                    className="grid grid-cols-[minmax(0,14rem)_1fr] items-baseline gap-4 py-2 first:pt-0 last:pb-0"
                  >
                    <dt className="text-muted-foreground font-mono text-xs break-all">{s.key}</dt>
                    <dd className="text-foreground min-w-0 font-mono text-xs break-all">{s.value}</dd>
                  </div>
                ))}
              </dl>
            )}
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
