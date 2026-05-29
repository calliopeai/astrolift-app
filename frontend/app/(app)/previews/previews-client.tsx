"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  ExternalLinkIcon,
  GitPullRequestIcon,
  TrashIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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
import { useFormatters } from "@/lib/i18n/formatters";
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

export function PreviewsClient() {
  const t = useTranslations("lists.previews");
  const fmt = useFormatters();
  const { can } = useMyPermissions();
  const formatTime = (iso: string | null | undefined): string =>
    iso ? fmt.formatDateTime(iso) : "—";
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

  const [tearTarget, setTearTarget] = React.useState<AstroliftPreviewEnvironment | null>(null);

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
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
                  <TableHead></TableHead>
                  <TableHead>{t("columns.app")}</TableHead>
                  <TableHead>{t("columns.pr")}</TableHead>
                  <TableHead>{t("columns.branch")}</TableHead>
                  <TableHead>{t("columns.hostname")}</TableHead>
                  <TableHead>{t("columns.lastDeploy")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  <TableHead className="text-right">{t("columns.actions")}</TableHead>
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
                            onClick={() => setTearTarget(p)}
                          >
                            <TrashIcon className="size-3" /> {t("tearDown")}
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

      <ConfirmDialog
        open={tearTarget !== null}
        onOpenChange={(next) => {
          if (!next) setTearTarget(null);
        }}
        title={
          tearTarget
            ? t("confirmTitle", { pr: tearTarget.prNumber })
            : t("confirmFallback")
        }
        description={
          tearTarget
            ? t("confirmDescription", {
                namespace: tearTarget.namespace,
                hostname: tearTarget.hostname,
                branch: tearTarget.branch,
              })
            : t("confirmDescriptionFallback")
        }
        confirmLabel={t("tearDown")}
        destructive
        onConfirm={async () => {
          if (!tearTarget) return;
          const { data: result } = await tearDown({
            variables: { input: { id: tearTarget.id } },
          });
          const r = result?.tearDownPreview;
          if (r?.ok) {
            toast.success(`Teardown enqueued`);
          } else {
            throw new Error(r?.errors[0]?.message ?? "Teardown failed");
          }
        }}
      />
    </PageShell>
  );
}
