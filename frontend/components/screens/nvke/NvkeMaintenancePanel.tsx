"use client";

import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type { GetNvkeMaintenanceQuery } from "@/graphql/__generated__/operations";

export type NvkeMaintenanceObservation =
  GetNvkeMaintenanceQuery["astroliftNvkeMaintenanceObservation"];
export interface NvkeMaintenancePanelProps {
  observation: NvkeMaintenanceObservation | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}
export function NvkeMaintenancePanel({
  observation: data,
  loading,
  error,
  onRefresh,
}: NvkeMaintenancePanelProps) {
  const t = useTranslations("nvke.maintenance"),
    common = useTranslations("nvke");
  const text = (value: string | null | undefined) =>
    value ? <code className="text-xs [overflow-wrap:anywhere]">{value}</code> : t("notReported");
  const bool = (value: boolean | null | undefined) =>
    value == null ? t("notReported") : t(value ? "yes" : "no");
  const state = (value: string | null | undefined) =>
    value && t.has(`values.${value}`) ? t(`values.${value}`) : t("notReported");
  return (
    <Section title={t("title")} description={t("help")}>
      <Button variant="outline" disabled={loading} onClick={onRefresh}>
        {common("refresh")}
      </Button>
      {error && <p role="alert">{error}</p>}
      {loading && !data && <Skeleton className="h-32" />}
      {data && (
        <>
          <p role="note" className="text-muted-foreground text-sm">
            {common("historicalHelp")}{" "}
            {data.observedAt ? (
              <time dateTime={data.observedAt}>{data.observedAt}</time>
            ) : (
              t("notReported")
            )}
          </p>
          <DefinitionList
            items={[
              { term: t("clusterId"), description: text(data.clusterId) },
              { term: t("nextCheck"), description: text(data.nextCertificateRenewalDue) },
              { term: t("overdue"), description: bool(data.renewalScheduleOverdueAtObservation) },
              { term: t("lockReported"), description: bool(data.mutationLockReported) },
              { term: t("lockOwner"), description: state(data.mutationLockOwner) },
              { term: t("lockStarted"), description: text(data.mutationLockStartedAt) },
            ]}
          />
          <Section title={t("renewal")} headingLevel="h3">
            {data.renewal ? (
              <DefinitionList
                items={[
                  { term: t("workflow"), description: text(data.renewal.workflowId) },
                  { term: t("state"), description: state(data.renewal.state) },
                  { term: t("outcome"), description: state(data.renewal.outcome) },
                  { term: t("forced"), description: bool(data.renewal.forced) },
                  { term: t("scheduled"), description: text(data.renewal.scheduledFor) },
                  { term: t("started"), description: text(data.renewal.startedAt) },
                  { term: t("completed"), description: text(data.renewal.completedAt) },
                  { term: t("failedNode"), description: text(data.renewal.failedNodeId) },
                  { term: t("phase"), description: state(data.renewal.phase) },
                  { term: t("retained"), description: bool(data.renewal.retainedMutationLock) },
                ]}
              />
            ) : (
              <p>{t("noRenewal")}</p>
            )}
          </Section>
          <Section title={t("rotation")} headingLevel="h3">
            {data.etcdKeyRotation ? (
              <DefinitionList
                items={[
                  { term: t("workflow"), description: text(data.etcdKeyRotation.workflowId) },
                  { term: t("state"), description: state(data.etcdKeyRotation.state) },
                  { term: t("phase"), description: state(data.etcdKeyRotation.phase) },
                  { term: t("started"), description: text(data.etcdKeyRotation.startedAt) },
                  { term: t("progress"), description: text(data.etcdKeyRotation.lastProgressAt) },
                  { term: t("completed"), description: text(data.etcdKeyRotation.completedAt) },
                  {
                    term: t("failureReason"),
                    description: state(data.etcdKeyRotation.failureReason),
                  },
                  {
                    term: t("retained"),
                    description: bool(data.etcdKeyRotation.retainedMutationLock),
                  },
                ]}
              />
            ) : (
              <p>{t("noRotation")}</p>
            )}
          </Section>
        </>
      )}
    </Section>
  );
}
