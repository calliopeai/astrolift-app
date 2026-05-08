"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  ExternalLinkIcon,
  GitPullRequestIcon,
  TrashIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TEAR_DOWN_PREVIEW } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_PREVIEW_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftPreviewEnvironment,
  PreviewStatus,
} from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface MutationResultLite {
  ok: boolean;
  errors: { code: string; message: string }[];
}

interface Resp {
  astroliftPreviewEnvironments: AstroliftPreviewEnvironment[];
}

const statusToDot: Record<
  PreviewStatus,
  "ok" | "warn" | "error" | "muted" | "pending"
> = {
  building: "pending",
  running: "ok",
  failed: "error",
  torn_down: "muted",
};

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function PreviewsClient() {
  const { can } = useMyPermissions();
  const { data, loading } = useQuery<Resp>(LIST_PREVIEW_ENVIRONMENTS, {
    variables: { appSlug: null },
    pollInterval: 30000,
  });
  const list = data?.astroliftPreviewEnvironments ?? [];

  const refetch = [
    { query: LIST_PREVIEW_ENVIRONMENTS, variables: { appSlug: null } },
  ];
  const [tearDown, tearState] = useMutation<{
    tearDownPreview: MutationResultLite;
  }>(TEAR_DOWN_PREVIEW, { refetchQueries: refetch });

  return (
    <PageShell
      title="Preview environments"
      description="Per-PR ephemeral deploys. Hostnames follow pr-<n>-<app>.pr.<org>.<base-zone>; tear-down on PR close."
    >
      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<GitPullRequestIcon className="size-5" />}
                title="No preview environments"
                description="Open a pull request against an app with preview_enabled=true to spin one up."
                actionHref="/apps"
                actionLabel="Open apps"
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>App</TableHead>
                  <TableHead>PR</TableHead>
                  <TableHead>Branch</TableHead>
                  <TableHead>Hostname</TableHead>
                  <TableHead>Last deploy</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="w-8">
                      <StatusDot status={statusToDot[p.status]} />
                    </TableCell>
                    <TableCell>
                      <div className="font-medium">{p.registeredAppSlug}</div>
                      <div className="text-muted-foreground text-xs font-mono">
                        ns {p.namespace}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      #{p.prNumber}
                    </TableCell>
                    <TableCell>
                      <div className="text-sm">{p.branch}</div>
                      {p.commitSha && (
                        <div className="text-muted-foreground text-xs font-mono">
                          {p.commitSha.slice(0, 7)}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      {p.status === "running" ? (
                        <a
                          href={`https://${p.hostname}`}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-sm hover:underline"
                        >
                          {p.hostname}
                          <ExternalLinkIcon className="size-3" />
                        </a>
                      ) : (
                        <span className="text-muted-foreground font-mono text-xs">
                          {p.hostname}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {formatTime(p.lastDeployedAt)}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {p.status.replace(/_/g, " ")}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      {p.status !== "torn_down" && can("app.deploy") && (
                        <Can permission="app.deploy">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={tearState.loading}
                            onClick={async () => {
                              if (
                                !confirm(
                                  `Tear down preview env for PR #${p.prNumber}? Namespace ${p.namespace} will be cleaned up.`,
                                )
                              )
                                return;
                              const { data: result } = await tearDown({
                                variables: { input: { id: p.id } },
                              });
                              const r = result?.tearDownPreview;
                              if (r?.ok) {
                                toast.success(`Teardown enqueued`);
                              } else {
                                toast.error(
                                  r?.errors[0]?.message ?? "Teardown failed",
                                );
                              }
                            }}
                          >
                            <TrashIcon className="size-3" /> Tear down
                          </Button>
                        </Can>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}
