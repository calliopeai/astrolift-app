"use client";

import { ActivityIcon, CircleDollarSignIcon, CoinsIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";

import { QueryError } from "@/components/QueryError";
import { Card, CardContent } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";

import { ZentinelleScopeFilter, type ZentinelleScopeFilterProps } from "./ZentinelleScopeFilter";
import { type ReadState, usageTotals, type ZentinelleUsageRow } from "./zentinelle-model";

export interface ZentinelleUsageProps {
  filter: ZentinelleScopeFilterProps;
  usage: ReadState<ZentinelleUsageRow[]>;
}

/** Model usage and cost from Zentinelle's gateway metering, per agent and model. */
export function ZentinelleUsage({ filter, usage }: ZentinelleUsageProps) {
  const t = useTranslations("zentinelleScreen.usage");
  const format = useFormatter();
  const totals = usageTotals(usage.data);
  const usd = (n: number) => format.number(n, { style: "currency", currency: "USD" });

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ZentinelleScopeFilter {...filter} />
      {usage.error ? (
        <QueryError title={t("error")} error={usage.error} onRetry={usage.onRetry} />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <StatTile
              label={t("requests")}
              value={format.number(totals.requests)}
              icon={ActivityIcon}
              loading={usage.loading}
            />
            <StatTile
              label={t("tokens")}
              value={format.number(totals.inputTokens + totals.outputTokens)}
              icon={CoinsIcon}
              loading={usage.loading}
            />
            <StatTile
              label={t("cost")}
              value={usd(totals.costUsd)}
              icon={CircleDollarSignIcon}
              loading={usage.loading}
            />
          </div>
          <Section title={t("title")}>
            <Card>
              <CardContent className="flex min-w-0 flex-col">
                {usage.loading && usage.data.length === 0 ? (
                  <Skeleton className="my-3 h-10 w-full" />
                ) : usage.data.length === 0 ? (
                  <p className="text-muted-foreground py-6 text-center text-sm">{t("empty")}</p>
                ) : (
                  usage.data.map((row) => (
                    <div
                      key={`${row.agentSlug}:${row.provider}:${row.model}`}
                      className="border-border/60 flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 border-b py-3 last:border-b-0"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-sm [overflow-wrap:anywhere]">
                          {row.agentSlug || t("noAgent")}
                        </div>
                        <div className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
                          {row.provider} · {row.model}
                        </div>
                      </div>
                      <span className="text-sm tabular-nums">
                        {t("requestCount", { count: row.requests })}
                      </span>
                      <span className="text-muted-foreground text-xs tabular-nums">
                        {t("tokenCount", {
                          input: format.number(row.inputTokens),
                          output: format.number(row.outputTokens),
                        })}
                      </span>
                      <span className="w-24 text-right text-sm font-medium tabular-nums">
                        {usd(row.costUsd)}
                      </span>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>
          </Section>
        </>
      )}
    </div>
  );
}
