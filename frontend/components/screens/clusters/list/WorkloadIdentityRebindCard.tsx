"use client";

import { AlertTriangleIcon, CheckCircleIcon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import type { AstroliftWorkloadIdentityRebind } from "@/graphql/__generated__/schema";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

type RebindState = "pending" | "running" | "succeeded" | "failed";

const STATE: Record<
  RebindState,
  { variant: "default" | "secondary" | "outline" | "destructive"; icon: React.ReactNode }
> = {
  pending: { variant: "outline", icon: <Loader2Icon className="size-3 animate-spin" /> },
  running: { variant: "secondary", icon: <Loader2Icon className="size-3 animate-spin" /> },
  succeeded: { variant: "default", icon: <CheckCircleIcon className="size-3" /> },
  failed: { variant: "destructive", icon: <AlertTriangleIcon className="size-3" /> },
};

export type WorkloadIdentityRebind = Pick<
  AstroliftWorkloadIdentityRebind,
  | "state"
  | "issuer"
  | "appsTotal"
  | "appsReconciled"
  | "failures"
  | "error"
  | "requestedAt"
  | "finishedAt"
>;

export type WorkloadIdentityRebindCardProps = { rebind: WorkloadIdentityRebind };

/**
 * The cluster's per-app IAM roles moving to its OIDC issuer after a
 * registration re-pointed it at another cluster (#2472): how far the move
 * got, and each app whose role did not move, with the reason.
 */
export function WorkloadIdentityRebindCard({ rebind }: WorkloadIdentityRebindCardProps) {
  const t = useTranslations("clusters.detail.workloadIdentity");
  const fmt = useFormatters();
  const state: RebindState = rebind.state in STATE ? (rebind.state as RebindState) : "pending";
  const tone = STATE[state];
  const reached = rebind.appsReconciled + rebind.failures.length;

  return (
    <Card
      className={cn(
        "!rounded-none border-l-4 shadow-md",
        state === "failed" ? "border-l-destructive" : "border-l-muted-foreground"
      )}
    >
      <CardHeader className="pb-3">
        <div className="flex min-w-0 items-start justify-between gap-4">
          <div className="min-w-0">
            <CardTitle className="text-base">{t("title")}</CardTitle>
            <CardDescription className="mt-0.5">{t(`description.${state}`)}</CardDescription>
          </div>
          <Badge variant={tone.variant} className="shrink-0 gap-1">
            {tone.icon}
            {t(`state.${state}`)}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="flex min-w-0 flex-col gap-4">
        <DefinitionList
          items={[
            {
              term: t("issuer"),
              description: (
                <span className="font-mono [overflow-wrap:anywhere]">{rebind.issuer}</span>
              ),
            },
            {
              term: t("apps"),
              description: (
                <span className="tabular-nums">
                  {t("progress", { reached, total: rebind.appsTotal })}
                </span>
              ),
            },
            {
              term: rebind.finishedAt ? t("finished") : t("started"),
              description: fmt.formatDateTime(rebind.finishedAt ?? rebind.requestedAt),
            },
          ]}
        />
        {rebind.error && (
          <p className="text-destructive text-sm [overflow-wrap:anywhere]">{rebind.error}</p>
        )}
        {rebind.failures.length > 0 && (
          <div className="min-w-0 text-sm">
            <p className="font-medium">{t("failuresTitle", { count: rebind.failures.length })}</p>
            <ul className="mt-2 flex flex-col gap-2">
              {rebind.failures.map((failure) => (
                <li key={`${failure.app}/${failure.role}`} className="min-w-0">
                  <p className="font-mono [overflow-wrap:anywhere]">
                    {failure.app} <span className="text-muted-foreground">({failure.role})</span>
                  </p>
                  <p className="text-destructive text-xs [overflow-wrap:anywhere]">
                    {failure.error}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
