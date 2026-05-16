"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  CloudIcon,
  ExternalLinkIcon,
  PauseIcon,
  PlayIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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

export function EnvironmentsClient({ appSlug }: { appSlug?: string } = {}) {
  const t = useTranslations("lists.environments");
  const { can } = useMyPermissions();
  const variables = { appSlug: appSlug ?? null };
  const { data, loading } = useQuery<Resp>(LIST_ENVIRONMENTS, {
    variables,
    pollInterval: 30000,
  });
  const list = data?.astroliftEnvironments ?? [];

  const refetch = [{ query: LIST_ENVIRONMENTS, variables }];
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
      throw new Error(result.errors[0]?.message ?? `${label} failed`);
    }
  }

  const canPause = can("app.deploy");
  const [pauseTarget, setPauseTarget] = React.useState<AstroliftAppEnvironment | null>(null);

  return (
    <PageShell
      title={t("title")}
      description={
        appSlug ? t("descriptionForApp", { slug: appSlug }) : t("description")
      }
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
                title={t("emptyTitle")}
                description={t("emptyDescription")}
                actionHref="/apps"
                actionLabel={t("openApps")}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columns.appEnv")}</TableHead>
                  <TableHead>{t("columns.url")}</TableHead>
                  <TableHead>{t("columns.cluster")}</TableHead>
                  <TableHead>{t("columns.approvals")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  <TableHead className="text-right">{t("columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell>
                      <div className="font-medium">{e.registeredAppSlug}</div>
                      <div className="text-muted-foreground text-xs">
                        {t.rich("envName", {
                          name: () => <span className="font-mono">{e.name}</span>,
                        })}
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
                          <PauseIcon className="size-3" /> {t("paused")}
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="gap-1">
                          <PlayIcon className="size-3" /> {t("active")}
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
                              <PlayIcon className="size-3" /> {t("resume")}
                            </Button>
                          </Can>
                        ) : (
                          <Can permission="app.deploy">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={() => setPauseTarget(e)}
                            >
                              <PauseIcon className="size-3" /> {t("pause")}
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

      <ConfirmDialog
        open={pauseTarget !== null}
        onOpenChange={(next) => {
          if (!next) setPauseTarget(null);
        }}
        title={
          pauseTarget
            ? t("confirmPause.title", {
                app: pauseTarget.registeredAppSlug,
                env: pauseTarget.name,
              })
            : t("confirmPause.fallbackTitle")
        }
        description={t("confirmPause.description")}
        confirmLabel={t("confirmPause.confirm")}
        destructive
        onConfirm={async () => {
          if (!pauseTarget) return;
          const { data } = await pause({ variables: { input: { id: pauseTarget.id } } });
          reportResult("pauseEnvironment", data?.pauseEnvironment);
        }}
      />
    </PageShell>
  );
}
