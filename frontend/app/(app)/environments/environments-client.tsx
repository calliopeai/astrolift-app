"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  CloudIcon,
  ExternalLinkIcon,
  PauseIcon,
  PlayIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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
import {
  PAUSE_ENVIRONMENT,
  RESUME_ENVIRONMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

interface Resp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

export function EnvironmentsClient() {
  const { can } = useMyPermissions();
  const { data, loading } = useQuery<Resp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: null },
    pollInterval: 30000,
  });
  const list = data?.astroliftEnvironments ?? [];

  const refetch = [{ query: LIST_ENVIRONMENTS, variables: { appSlug: null } }];
  const [pause, pauseState] = useMutation<{
    pauseEnvironment: MutationResultLite<AstroliftAppEnvironment>;
  }>(PAUSE_ENVIRONMENT, { refetchQueries: refetch });
  const [resume, resumeState] = useMutation<{
    resumeEnvironment: MutationResultLite<AstroliftAppEnvironment>;
  }>(RESUME_ENVIRONMENT, { refetchQueries: refetch });
  const busy = pauseState.loading || resumeState.loading;

  function reportResult(
    label: string,
    result: MutationResultLite<AstroliftAppEnvironment> | null | undefined,
  ) {
    if (!result) return;
    if (result.ok) {
      toast.success(
        `${label}: ${result.data?.deploysPaused ? "paused" : "active"}`,
      );
    } else {
      toast.error(result.errors[0]?.message ?? `${label} failed`);
    }
  }

  const canPause = can("app.deploy");

  return (
    <PageShell
      title="Environments"
      description="Deploy targets across your apps. Pause an environment to halt CI/push triggers and require explicit operator action to resume."
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
                icon={<CloudIcon className="size-5" />}
                title="No environments yet"
                description="Register an app and bind it to a cluster to create your first environment."
                actionHref="/apps"
                actionLabel="Open apps"
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>App / Env</TableHead>
                  <TableHead>URL</TableHead>
                  <TableHead>Cluster</TableHead>
                  <TableHead>Approvals</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell>
                      <div className="font-medium">{e.registeredAppSlug}</div>
                      <div className="text-muted-foreground text-xs">
                        env <span className="font-mono">{e.name}</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      {e.url ? (
                        <a
                          href={e.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-sm hover:underline"
                        >
                          {e.url} <ExternalLinkIcon className="size-3" />
                        </a>
                      ) : (
                        <span className="text-muted-foreground text-sm">—</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {e.clusterSlug ?? "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {e.requiredApprovals}
                    </TableCell>
                    <TableCell>
                      {e.deploysPaused ? (
                        <Badge variant="destructive" className="gap-1">
                          <PauseIcon className="size-3" /> paused
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="gap-1">
                          <PlayIcon className="size-3" /> active
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {canPause &&
                        (e.deploysPaused ? (
                          <Can permission="app.deploy">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={async () => {
                                const { data } = await resume({
                                  variables: { input: { id: e.id } },
                                });
                                reportResult(
                                  "resumeEnvironment",
                                  data?.resumeEnvironment,
                                );
                              }}
                            >
                              <PlayIcon className="size-3" /> Resume
                            </Button>
                          </Can>
                        ) : (
                          <Can permission="app.deploy">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={async () => {
                                if (
                                  !confirm(
                                    `Pause ${e.registeredAppSlug}/${e.name}? CI and push triggers will be rejected until resumed.`,
                                  )
                                )
                                  return;
                                const { data } = await pause({
                                  variables: { input: { id: e.id } },
                                });
                                reportResult(
                                  "pauseEnvironment",
                                  data?.pauseEnvironment,
                                );
                              }}
                            >
                              <PauseIcon className="size-3" /> Pause
                            </Button>
                          </Can>
                        ))}
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
