"use client";

/**
 * WorkloadIdentityCard — operator-facing IRSA / Workload Identity /
 * Federated Identity binding panel for the app detail observability
 * subroute (#377).
 *
 * Shows the role ARN (or principal), the trust-policy summary line,
 * and the cloud's last-used timestamp when available. The empty state
 * is keyed off the resolver's `reason` (#1111) so "not configured",
 * "not available on this cloud", and "no binding yet" read distinctly.
 */

import { KeyRoundIcon, RefreshCwIcon, RotateCcwIcon } from "lucide-react";
import * as React from "react";
import { useFormatter, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import {
  type ObservabilityPanelReason,
  panelEmptyState,
} from "@/components/observability/panel-reason";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAppIdentityBinding } from "@/graphql/lifecycle/lifecycle.types";

export interface WorkloadIdentityCardData {
  astroliftAppIdentityBinding: {
    reason: ObservabilityPanelReason;
    binding: AstroliftAppIdentityBinding | null;
  };
}

function formatLastUsed(
  iso: string | null | undefined,
  formatDateTime: (d: Date) => string
): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return formatDateTime(d);
}

export interface WorkloadIdentityCardProps {
  appSlug: string;
}

/** Pure (Storybook first): the data comes from useWorkloadIdentity. */
export function WorkloadIdentityCard({
  appSlug,
  data,
  loading,
  onRefresh,
}: WorkloadIdentityCardProps & {
  data: WorkloadIdentityCardData | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  const t = useTranslations("obsWorkloadIdentity");
  const reasonT = useTranslations("obsPanelReason");
  const format = useFormatter();
  const binding = data?.astroliftAppIdentityBinding?.binding ?? null;
  const reason = data?.astroliftAppIdentityBinding?.reason;
  const isEmptyAfterLoad = !loading && binding === null;
  const empty = panelEmptyState(
    reason ?? "NO_DATA_YET",
    {
      noData: { title: t("noDataTitle"), description: t("noDataDescription") },
      notConfigured: t("notConfigured"),
    },
    reasonT
  );
  const showConfigureAction =
    reason === "NOT_CONFIGURED" || reason === "NO_DATA_YET" || reason == null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRoundIcon className="size-4" /> {t("title")}
          </CardTitle>
          <CardDescription>
            {t.rich("description", {
              method: "describe_identity",
              code: (chunks) => <code className="text-2xs font-mono">{chunks}</code>,
            })}
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              onRefresh();
            }}
            disabled={loading}
          >
            <RefreshCwIcon className="size-3" /> {t("refresh")}
          </Button>
          <Can permission="app.update">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                // Force re-bind = re-apply the SA annotation that the
                // controller (eks-pod-identity, gke-wi, aks-fic) reads.
                // We don't yet have a generic "rebind WI" mutation in
                // the lifecycle surface; surface intent via a toast so
                // operators know the next deploy will re-stamp the
                // annotation.
                toast.message(t("forceRebind"), {
                  description: t("forceRebindDescription"),
                });
              }}
            >
              <RotateCcwIcon className="size-3" /> {t("forceRebind")}
            </Button>
          </Can>
        </div>
      </CardHeader>
      <CardContent>
        {loading && binding === null ? (
          <div className="space-y-2">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-1/2" />
          </div>
        ) : isEmptyAfterLoad ? (
          <EmptyState
            icon={<KeyRoundIcon className="size-5" />}
            title={empty.title}
            description={empty.description}
            actionHref={showConfigureAction ? `/apps/${appSlug}/security` : undefined}
            actionLabel={showConfigureAction ? t("configure") : undefined}
            secondary={
              reason === "ERROR" ? (
                <Button size="sm" variant="outline" onClick={() => onRefresh()}>
                  {t("tryAgain")}
                </Button>
              ) : undefined
            }
          />
        ) : binding ? (
          <dl className="grid grid-cols-1 gap-4 text-sm md:grid-cols-2">
            <div>
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">{t("kind")}</dt>
              <dd className="mt-1">
                <Badge variant="secondary" className="font-mono text-xs">
                  {t.has(`kinds.${binding.kind}`) ? t(`kinds.${binding.kind}`) : binding.kind}
                </Badge>
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">
                {t("lastUsed")}
              </dt>
              <dd className="text-muted-foreground mt-1 font-mono text-xs">
                {formatLastUsed(binding.lastUsedAt, (d) =>
                  format.dateTime(d, { dateStyle: "medium", timeStyle: "medium" })
                )}
              </dd>
            </div>
            <div className="md:col-span-2">
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">
                {t("rolePrincipal")}
              </dt>
              <dd className="mt-1 font-mono text-xs break-all">{binding.roleArnOrPrincipal}</dd>
            </div>
            <div className="md:col-span-2">
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">
                {t("trustPolicy")}
              </dt>
              <dd className="text-muted-foreground mt-1 font-mono text-xs">
                {binding.trustPolicySummary}
              </dd>
            </div>
          </dl>
        ) : null}
      </CardContent>
    </Card>
  );
}
