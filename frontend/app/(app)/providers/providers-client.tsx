"use client";

import { useQuery } from "@apollo/client/react";
import { CheckCircle2Icon, CloudIcon, XCircleIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { ViewToggle } from "@/components/ViewToggle";
import { useViewToggle } from "@/hooks/use-view-toggle";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_PROVIDER_PLUGINS } from "@/graphql/clusters/clusters.queries";
import type { AstroliftProviderPlugin } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftProviderPlugins: AstroliftProviderPlugin[];
}

export function ProvidersClient() {
  const { data, loading } = useQuery<Resp>(LIST_PROVIDER_PLUGINS);
  const list = data?.astroliftProviderPlugins ?? [];
  const [viewMode, setViewMode] = useViewToggle("astrolift_view_providers", "card");

  return (
    <PageShell
      title="Provider plugins"
      description="Driver implementations registered with the platform — per-cloud bundles of ClusterDriver, IngressDriver, DnsDriver, and so on."
      actions={<ViewToggle mode={viewMode} onChange={setViewMode} />}
    >
      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
        </div>
      ) : list.length === 0 ? (
        <Card>
          <CardContent className="p-6">
            <EmptyState
              icon={<CloudIcon className="size-5" />}
              title="No plugins registered"
              description="Provider plugins live in astrolift-providers/ and load on worker startup. Check that ASTROLIFT_PROVIDERS includes the plugin id."
            />
          </CardContent>
        </Card>
      ) : viewMode === "list" ? (
        <div className="flex flex-col gap-1">
          {list.map((p) => {
            const drivers = Object.keys(p.capabilitiesManifest ?? {});
            return (
              <div key={p.id} className="hover:bg-accent/50 flex items-center gap-3 rounded-md border px-4 py-2.5 transition-colors">
                {p.isEnabled ? (
                  <CheckCircle2Icon className="text-emerald-600 size-4 shrink-0" />
                ) : (
                  <XCircleIcon className="text-muted-foreground size-4 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{p.name}</span>
                  <span className="text-muted-foreground ml-2 font-mono text-xs">{p.slug} · v{p.version}</span>
                </div>
                <Badge variant="outline" className="shrink-0 text-xs">
                  {drivers.length} driver{drivers.length === 1 ? "" : "s"}
                </Badge>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {list.map((p) => {
            const drivers = Object.keys(p.capabilitiesManifest ?? {});
            return (
              <Card key={p.id}>
                <CardHeader className="flex flex-row items-start gap-3 space-y-0">
                  <div className="bg-primary/10 text-primary rounded-md p-2">
                    <CloudIcon className="size-4" />
                  </div>
                  <div className="flex-1">
                    <CardTitle className="text-base">{p.name}</CardTitle>
                    <CardDescription className="font-mono text-xs">
                      {p.slug} · v{p.version}
                    </CardDescription>
                  </div>
                  {p.isEnabled ? (
                    <CheckCircle2Icon className="text-emerald-600 size-4" />
                  ) : (
                    <XCircleIcon className="text-muted-foreground size-4" />
                  )}
                </CardHeader>
                <CardContent>
                  <p className="text-muted-foreground text-xs">
                    {drivers.length} driver{drivers.length === 1 ? "" : "s"}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {drivers.slice(0, 8).map((d) => (
                      <Badge key={d} variant="outline" className="text-xs">
                        {d}
                      </Badge>
                    ))}
                    {drivers.length > 8 && (
                      <Badge variant="secondary" className="text-xs">
                        +{drivers.length - 8}
                      </Badge>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </PageShell>
  );
}
