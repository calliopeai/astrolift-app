"use client";

import { DatabaseIcon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AstroliftRetentionPolicy } from "@/graphql/registry/registry.types";

import {
  RETENTION_DAY_OPTIONS,
  RETENTION_DEFAULT_DAYS,
  RETENTION_SIGNALS,
  retentionSignalLabel,
  type useRetentionPolicy,
} from "./use-retention-policy";

export type RetentionPolicyViewProps = ReturnType<typeof useRetentionPolicy> & {
  policies: AstroliftRetentionPolicy[];
  appId?: string;
  sourceVersion?: number | null;
};

/** How long observability data is kept per signal type. */
export function RetentionPolicyView({
  saving,
  onChange,
  policies,
  appId,
  sourceVersion,
}: RetentionPolicyViewProps) {
  const t = useTranslations("apps.settings.retentionFlow");
  const policyMap = React.useMemo(
    () => Object.fromEntries(policies.map((p) => [p.signal, p.retentionDays])),
    [policies]
  );

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <DatabaseIcon className="text-muted-foreground size-4 shrink-0" />
          {t("title")}
        </span>
      }
      description={t("description", { days: RETENTION_DEFAULT_DAYS })}
    >
      <div className="flex flex-col gap-2">
        {RETENTION_SIGNALS.map(({ signal }) => {
          const label = retentionSignalLabel(signal, t);
          const current = policyMap[signal] ?? RETENTION_DEFAULT_DAYS;
          const isDefault = policyMap[signal] === undefined;
          const isSaving = !!saving[signal];
          return (
            <div
              key={signal}
              className="bg-card flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-md border p-3"
            >
              <div className="flex items-center gap-3">
                <Label className="w-28 shrink-0 text-sm font-medium">{label}</Label>
                {isDefault ? (
                  <Badge variant="secondary" className="text-2xs">
                    {t("platformDefault")}
                  </Badge>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                {isSaving ? (
                  <Loader2Icon className="text-muted-foreground size-3.5 animate-spin" />
                ) : null}
                <Can permission="app.update">
                  <Select
                    key={JSON.stringify([appId, sourceVersion, policies, signal])}
                    value={String(current)}
                    onValueChange={(v) => void onChange(signal, v)}
                    disabled={isSaving}
                  >
                    <SelectTrigger
                      className="w-36"
                      aria-label={t("selectorLabel", { signal: label })}
                    >
                      <SelectValue>{t("days", { days: current })}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {RETENTION_DAY_OPTIONS.map((d) => (
                        <SelectItem key={d} value={String(d)}>
                          {t("days", { days: d })}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Can>
              </div>
            </div>
          );
        })}
      </div>
    </Section>
  );
}
