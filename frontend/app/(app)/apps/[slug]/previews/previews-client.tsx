"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CalendarClockIcon,
  ExternalLinkIcon,
  GitPullRequestIcon,
  TrashIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface PreviewsResp {
  astroliftPreviewEnvironments: AstroliftPreviewEnvironment[];
}
interface MutationResultLite {
  ok: boolean;
  errors: { code: string; message: string }[];
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

// Stale-after threshold: anything still running this long after its last
// deploy is a candidate for cleanup. 7d is the default "feature work that
// should have merged by now" window — orgs that want longer can ignore
// the CTA, orgs that want shorter aren't penalized.
const STALE_DAYS = 7;
const STALE_THRESHOLD_MS = STALE_DAYS * 24 * 60 * 60 * 1000;

function isStale(p: AstroliftPreviewEnvironment): boolean {
  if (p.status !== "running") return false;
  if (!p.lastDeployedAt) return false;
  const last = new Date(p.lastDeployedAt).getTime();
  return Date.now() - last > STALE_THRESHOLD_MS;
}

export function AppPreviewsClient({ slug }: { slug: string }) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const previews = useQuery<PreviewsResp>(LIST_PREVIEW_ENVIRONMENTS, {
    variables: { appSlug: slug },
    pollInterval: 30000,
  });

  const [tearDown, tearState] = useMutation<{
    tearDownPreview: MutationResultLite;
  }>(TEAR_DOWN_PREVIEW, {
    refetchQueries: [
      { query: LIST_PREVIEW_ENVIRONMENTS, variables: { appSlug: slug } },
    ],
  });

  const a = app.data?.astroliftApp;
  const list = previews.data?.astroliftPreviewEnvironments ?? [];
  const active = list.filter((p) => p.status !== "torn_down");
  const stale = active.filter(isStale);
  const [tearDownTarget, setTearDownTarget] = React.useState<AstroliftPreviewEnvironment | null>(
    null,
  );

  if (app.loading && !a) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

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

  async function handleTearDown(p: AstroliftPreviewEnvironment) {
    const { data } = await tearDown({ variables: { input: { id: p.id } } });
    const r = data?.tearDownPreview;
    if (r?.ok) {
      toast.success(`Teardown enqueued for PR #${p.prNumber}`);
    } else {
      throw new Error(r?.errors[0]?.message ?? "Teardown failed");
    }
  }

  return (
    <PageShell
      title={`${a.name} · Previews`}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {a.slug} · per-PR ephemeral environments at pr-N.{a.subdomain}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="previews" />

      {!a.previewEnabled && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <AlertTriangleIcon className="mt-0.5 size-4 text-amber-700 dark:text-amber-300" />
            <div className="flex-1">
              <CardTitle className="text-sm">Previews are disabled for this app</CardTitle>
              <CardDescription>
                Enable per-PR previews in app settings to spin one up
                automatically when a pull request opens. You can still see
                historical preview rows below.
              </CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      {stale.length > 0 && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <CalendarClockIcon className="mt-0.5 size-4 text-amber-700 dark:text-amber-300" />
            <div className="flex-1">
              <CardTitle className="text-sm">
                {stale.length} preview{stale.length === 1 ? "" : "s"} stale
              </CardTitle>
              <CardDescription>
                These previews haven&apos;t been redeployed in more than{" "}
                {STALE_DAYS} days. Either the PR is dormant or auto-teardown
                missed it — consider tearing them down to free namespace
                quota.
              </CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      <Card>
        <CardContent className="p-0">
          {previews.loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<GitPullRequestIcon className="size-5" />}
                title="No preview environments yet"
                description={
                  a.previewEnabled
                    ? "Open a pull request against this app to spin up a preview automatically."
                    : "Enable previews in app settings to spin one up automatically on each PR."
                }
                actionHref={
                  a.previewEnabled ? a.sourceUrl ?? undefined : `/apps/${a.slug}/config`
                }
                actionLabel={
                  a.previewEnabled
                    ? a.sourceUrl
                      ? "Open repo"
                      : undefined
                    : "Enable previews"
                }
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-6"></TableHead>
                  <TableHead>PR / branch</TableHead>
                  <TableHead>Hostname</TableHead>
                  <TableHead>Last deploy</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="w-6">
                      <StatusDot status={statusToDot[p.status]} />
                    </TableCell>
                    <TableCell>
                      <div className="font-medium">
                        #{p.prNumber} · {p.branch}
                      </div>
                      <div className="text-muted-foreground font-mono text-xs">
                        ns {p.namespace}
                        {p.commitSha && (
                          <> · {p.commitSha.slice(0, 7)}</>
                        )}
                      </div>
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
                      {p.lastDeployedAt
                        ? new Date(p.lastDeployedAt).toLocaleString()
                        : "—"}
                      {isStale(p) && (
                        <Badge
                          variant="outline"
                          className="ml-2 text-[10px] uppercase"
                        >
                          stale
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {p.status.replace(/_/g, " ")}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      {p.status !== "torn_down" && (
                        <Can permission="app.deploy">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={tearState.loading}
                            onClick={() => setTearDownTarget(p)}
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

      <p className="text-muted-foreground text-center text-xs">
        Preview retention is {a.previewMaxActive} active env
        {a.previewMaxActive === 1 ? "" : "s"} per app · stale threshold{" "}
        {STALE_DAYS}d.
      </p>

      <ConfirmDialog
        open={tearDownTarget !== null}
        onOpenChange={(next) => {
          if (!next) setTearDownTarget(null);
        }}
        title={
          tearDownTarget
            ? `Tear down preview for PR #${tearDownTarget.prNumber}?`
            : "Tear down preview?"
        }
        description={
          tearDownTarget
            ? `Namespace ${tearDownTarget.namespace} will be deleted, its ingress hostname (${tearDownTarget.hostname}) released, and any managed-service rows scoped to this preview are deprovisioned. The preview re-spins automatically on the next push to ${tearDownTarget.branch}.`
            : "Namespace will be deleted and the hostname released."
        }
        confirmLabel="Tear down"
        destructive
        onConfirm={async () => {
          if (tearDownTarget) await handleTearDown(tearDownTarget);
        }}
      />
    </PageShell>
  );
}
