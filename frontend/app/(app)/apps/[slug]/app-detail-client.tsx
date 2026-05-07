"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  CheckCircle2Icon,
  ExternalLinkIcon,
  GitBranchIcon,
  Trash2Icon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { SOFT_DELETE_APP } from "@/graphql/registry/registry.mutations";
import {
  GET_APP,
  LIST_APPS,
  LIST_WORKLOADS,
} from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
  ProvisioningStatus,
} from "@/graphql/registry/registry.types";

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}

export function AppDetailClient({ slug }: { slug: string }) {
  const router = useRouter();
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteApp: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_APP, {
    refetchQueries: [{ query: LIST_APPS }],
    awaitRefetchQueries: true,
  });

  if (app.loading && !app.data) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  const a = app.data?.astroliftApp;
  if (!a) {
    return (
      <PageShell title="App not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No app with slug ${slug}`}
          description="It may have been soft-deleted, or you may not have permission to read it."
          actionHref="/apps"
          actionLabel="Back to apps"
        />
      </PageShell>
    );
  }

  async function handleDelete() {
    if (!a) return;
    if (!confirm(`Delete ${a.slug}? Soft delete only — its slug becomes reclaimable but workloads stay torn down.`)) {
      return;
    }
    const { data } = await softDelete({ variables: { input: { id: a.id } } });
    if (data?.softDeleteApp.ok) {
      toast.success(`Deleted ${a.slug}`);
      router.push("/apps");
    } else {
      toast.error(data?.softDeleteApp.errors?.[0]?.message ?? "Delete failed");
    }
  }

  const wlList = workloads.data?.astroliftWorkloads ?? [];

  return (
    <PageShell
      title={a.name}
      description={a.description || `Registered app · ${a.slug}`}
      actions={
        <>
          <Button variant="outline" asChild>
            {a.sourceUrl ? (
              <a href={a.sourceUrl} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-4" />
                Source
              </a>
            ) : (
              <span>
                <GitBranchIcon className="size-4" />
                {a.sourceRepo || "no source"}
              </span>
            )}
          </Button>
          <Button variant="ghost" onClick={handleDelete} disabled={deleting}>
            <Trash2Icon className="size-4" />
            Delete
          </Button>
        </>
      }
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-muted-foreground text-sm">Status</CardTitle>
            <StatusDot status={statusDot[a.provisioningStatus]} />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold capitalize">{a.provisioningStatus}</p>
            {a.provisioningError && (
              <p className="text-destructive mt-1 text-xs">{a.provisioningError}</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Source</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="font-mono text-sm">{a.sourceRepo || "—"}</p>
            <p className="text-muted-foreground text-xs">
              <span className="font-mono">{a.deployBranch}</span> · {a.manifestPath}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Trigger</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm capitalize">{a.triggerMode.replace(/_/g, " ")}</p>
            <p className="text-muted-foreground text-xs">
              k8s namespace: <span className="font-mono">{a.k8sNamespace}</span>
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Manifest</CardTitle>
          <CardDescription>
            Raw + normalized form persisted at registration time. Changes appear
            here on the next push to {a.deployBranch}.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {a.manifestHash ? (
            <p className="text-muted-foreground text-sm">
              Manifest hash:{" "}
              <span className="font-mono">{a.manifestHash.slice(0, 16)}…</span>
            </p>
          ) : (
            <EmptyState
              icon={<GitBranchIcon className="size-5" />}
              title="No manifest yet"
              description="The OnboardAppWorkflow fetches astrolift.toml on the first run; this card fills in once it's parsed."
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Workloads</CardTitle>
            <CardDescription>
              Deployments, statefulsets, jobs, and cronjobs declared in the manifest.
            </CardDescription>
          </div>
          <Badge variant="outline">{wlList.length}</Badge>
        </CardHeader>
        <CardContent className="p-0">
          {workloads.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : wlList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title="No workloads yet"
                description="Workloads appear here after the manifest sync activity runs."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Workload</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Replicas</TableHead>
                  <TableHead>CPU</TableHead>
                  <TableHead>Memory</TableHead>
                  <TableHead>Public</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {wlList.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell>
                      <div className="font-medium">{w.name}</div>
                      <div className="text-muted-foreground font-mono text-xs">
                        {w.slug}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{w.kind}</Badge>
                    </TableCell>
                    <TableCell>{w.replicas}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {w.cpuRequest || "—"} / {w.cpuLimit || "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {w.memoryRequest || "—"} / {w.memoryLimit || "—"}
                    </TableCell>
                    <TableCell>
                      {w.isPublic ? (
                        <CheckCircle2Icon className="text-emerald-600 size-4" />
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {[
          { label: "Environments", body: "Deploy targets, approval gates, ABAC policies." },
          { label: "Deployments", body: "Per-rollout timeline, logs, rendered manifests." },
          { label: "Secrets", body: "Per-env secret bundles + managed-service bindings." },
          { label: "Custom domains", body: "DNS validation + cert state." },
        ].map((s) => (
          <Card key={s.label} className="border-dashed">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{s.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground text-xs leading-relaxed">{s.body}</p>
              <p className="mt-2 text-xs italic">arrives next milestone</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </PageShell>
  );
}
