"use client";

import { ShieldIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { QueryError } from "@/components/QueryError";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { ZentinelleScopeFilter, type ZentinelleScopeFilterProps } from "./ZentinelleScopeFilter";
import type { ReadState, ZentinellePolicy } from "./zentinelle-model";

export interface ZentinellePoliciesProps {
  filter: ZentinelleScopeFilterProps;
  policies: ReadState<ZentinellePolicy[]>;
}

/** The Zentinelle policies in effect for the organization, an agent or a project. */
export function ZentinellePolicies({ filter, policies }: ZentinellePoliciesProps) {
  const t = useTranslations("zentinelleScreen.policies");

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <ZentinelleScopeFilter {...filter} teams={false} />
      {policies.error ? (
        <QueryError title={t("error")} error={policies.error} onRetry={policies.onRetry} />
      ) : (
        <Card>
          <CardContent className="flex min-w-0 flex-col">
            {policies.loading && policies.data.length === 0 ? (
              <Skeleton className="my-3 h-12 w-full" />
            ) : policies.data.length === 0 ? (
              <p className="text-muted-foreground py-6 text-center text-sm">{t("empty")}</p>
            ) : (
              policies.data.map((policy) => (
                <div
                  key={policy.id}
                  className="border-border/60 flex min-w-0 items-start gap-3 border-b py-3 last:border-b-0"
                >
                  <ShieldIcon className="text-primary mt-0.5 size-4 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="font-medium [overflow-wrap:anywhere]">{policy.name}</span>
                      <Badge variant="outline">{policy.policyType}</Badge>
                      <Badge variant="secondary">{policy.enforcement}</Badge>
                    </div>
                    {policy.description && (
                      <p className="text-muted-foreground mt-1 text-sm [overflow-wrap:anywhere]">
                        {policy.description}
                      </p>
                    )}
                    <p className="text-muted-foreground mt-1 text-xs">
                      {t("meta", { scope: policy.scope, priority: policy.priority })}
                    </p>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
