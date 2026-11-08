"use client";

import { KeyRoundIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type { Column, CursorTableController } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  GetNvkeCredentialObservationQuery,
  GetNvkeCredentialCertificatesQuery,
  GetNvkeCredentialMetricsQuery,
} from "@/graphql/__generated__/operations";

export type NvkeCredentialObservation = NonNullable<
  GetNvkeCredentialObservationQuery["astroliftNvkeCredentialObservation"]["data"]
>;
export type NvkeCredentialCertificate = NonNullable<
  GetNvkeCredentialCertificatesQuery["astroliftNvkeCredentialCertificatesPage"]["data"]
>["items"][number];
export type NvkeCredentialMetric = NonNullable<
  GetNvkeCredentialMetricsQuery["astroliftNvkeCredentialMetricsPage"]["data"]
>["items"][number];
export interface NvkeCredentialPanelProps {
  observation: NvkeCredentialObservation | null;
  certificates: CursorTableController<NvkeCredentialCertificate>;
  samples: CursorTableController<NvkeCredentialMetric>;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

export function NvkeCredentialPanel(props: NvkeCredentialPanelProps) {
  const t = useTranslations("nvke.credentialMetrics"),
    common = useTranslations("nvke");
  const certificates = useCursorTableList(props.certificates, {
    id: "nvke.credential.certificates",
    label: t("certificates"),
    searchPlaceholder: t("search"),
  });
  const samples = useCursorTableList(props.samples, {
    id: "nvke.credential.samples",
    label: t("samples"),
    searchPlaceholder: t("search"),
  });
  const bool = (value: boolean | null | undefined) =>
    value == null ? t("unknown") : t(value ? "yes" : "no");
  const text = (value: string | null | undefined) =>
    value == null ? (
      t("unknown")
    ) : (
      <code className="text-xs [overflow-wrap:anywhere]">{value}</code>
    );
  const certificateColumns: Column<NvkeCredentialCertificate>[] = [
    {
      id: "credential",
      header: t("credential"),
      cellClassName: "max-w-64",
      cell: (row) => (
        <div className="space-y-1">
          {text(row.credential)}
          <div>{text(row.node)}</div>
        </div>
      ),
    },
    { id: "required", header: t("required"), cell: (row) => bool(row.required) },
    { id: "notAfter", header: t("notAfter"), cell: (row) => text(row.reportedNotAfter) },
    { id: "expired", header: t("expired"), cell: (row) => bool(row.reportedExpiredAtScrape) },
    {
      id: "inspection",
      header: t("inspection"),
      cell: (row) => bool(row.latestInspectionAvailable),
    },
    { id: "available", header: t("available"), cell: (row) => bool(row.observationAvailable) },
    { id: "age", header: t("age"), cell: (row) => text(row.observationAgeSeconds) },
  ];
  const sampleColumns: Column<NvkeCredentialMetric>[] = [
    {
      id: "metric",
      header: t("metric"),
      cellClassName: "max-w-80",
      cell: (row) => text(row.metric),
    },
    { id: "node", header: t("node"), cellClassName: "max-w-64", cell: (row) => text(row.node) },
    {
      id: "credential",
      header: t("credential"),
      cellClassName: "max-w-64",
      cell: (row) => text(row.credential),
    },
    { id: "state", header: common("state"), cell: (row) => text(row.state) },
    { id: "value", header: t("value"), cell: (row) => text(row.value) },
  ];
  const data = !props.loading && !props.error ? props.observation : null;
  return (
    <Section title={t("title")} description={t("help")}>
      <div className="flex">
        <Button variant="outline" disabled={props.loading} onClick={props.onRefresh}>
          {common("refresh")}
        </Button>
      </div>
      {props.loading && <Skeleton className="h-32" />}
      {props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {props.error}
        </p>
      )}
      {data && (
        <>
          <p role="note" className="text-muted-foreground text-sm">
            {common("historicalHelp")} <time dateTime={data.scrapedAt}>{data.scrapedAt}</time>
          </p>
          <DefinitionList
            items={[
              { term: t("cluster"), description: text(data.clusterId) },
              { term: t("reported"), description: bool(data.clusterReported) },
              { term: t("collection"), description: bool(data.collectionSucceeded) },
              { term: t("monitoring"), description: bool(data.monitoringEnabled) },
              { term: t("automation"), description: bool(data.renewalAutomationEnabled) },
              { term: t("window"), description: text(data.warningWindowSeconds) },
            ]}
          />
          <h3 className="text-base font-semibold">{t("certificates")}</h3>
          <ListPage
            embedded
            list={certificates}
            label={t("certificates")}
            rows={props.certificates.rows}
            columns={certificateColumns}
            getRowId={(row) => row.key}
            loading={props.certificates.state === "loading"}
            stale={props.certificates.isStale}
            error={props.certificates.state === "error" ? { message: common("readFailed") } : null}
            onRetry={props.certificates.retry}
            nextCursor={props.certificates.nextCursor}
            totalCount={props.certificates.totalCount}
            empty={{ icon: <KeyRoundIcon />, title: t("empty"), description: t("help") }}
          />
          <h3 className="text-base font-semibold">{t("samples")}</h3>
          <ListPage
            embedded
            list={samples}
            label={t("samples")}
            rows={props.samples.rows}
            columns={sampleColumns}
            getRowId={(row) => row.key}
            loading={props.samples.state === "loading"}
            stale={props.samples.isStale}
            error={props.samples.state === "error" ? { message: common("readFailed") } : null}
            onRetry={props.samples.retry}
            nextCursor={props.samples.nextCursor}
            totalCount={props.samples.totalCount}
            empty={{ icon: <KeyRoundIcon />, title: t("empty"), description: t("help") }}
          />
        </>
      )}
    </Section>
  );
}
