"use client";

import { useQuery } from "@apollo/client/react";
import { CheckCircle2Icon, CloudIcon, XCircleIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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

export default function ProvidersPage() {
  const { data, loading } = useQuery<Resp>(LIST_PROVIDER_PLUGINS);
  const list = data?.astroliftProviderPlugins ?? [];

  return (
    <PageShell
      title="Provider plugins"
      description="Driver implementations registered with the platform — per-cloud bundles of ClusterDriver, IngressDriver, DnsDriver, and so on."
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
