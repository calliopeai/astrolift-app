"use client";
import { ActivityIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type { Column, CursorTableController } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  GetNvkeDiagnosticSummaryQuery,
  ListNvkeDiagnosticRowsQuery,
} from "@/graphql/__generated__/operations";
export type NvkeDiagnosticSummary = GetNvkeDiagnosticSummaryQuery["astroliftNvkeDiagnosticPage"];
export type NvkeDiagnosticRow =
  ListNvkeDiagnosticRowsQuery["astroliftNvkeDiagnosticPage"]["items"][number];
export interface NvkeDiagnosticsPanelProps {
  observation: NvkeDiagnosticSummary | null;
  table: CursorTableController<NvkeDiagnosticRow>;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}
export function NvkeDiagnosticsPanel(props: NvkeDiagnosticsPanelProps) {
  const t = useTranslations("nvke"),
    d = useTranslations("nvke.diagnostics");
  const { observation, table } = props;
  const summary = observation?.summary;
  const list = useCursorTableList(table, {
    id: "nvke.diagnostics",
    label: d("title"),
    searchPlaceholder: d("search"),
  });
  const bool = (value: boolean | null | undefined) =>
    value == null ? d("notReported") : d(value ? "yes" : "no");
  const text = (value: string | number | null | undefined) => value ?? d("notReported");
  const columns: Column<NvkeDiagnosticRow>[] =
    summary?.kind === "config_history"
      ? [
          {
            id: "identity",
            header: t("identity"),
            cellClassName: "max-w-64",
            cell: (row) => <code className="block truncate">{text(row.nativeId)}</code>,
          },
          {
            id: "created",
            header: d("createdAt"),
            cellClassName: "max-w-52",
            cell: (row) =>
              row.createdAt ? (
                <time dateTime={row.createdAt}>{row.createdAt}</time>
              ) : (
                d("notReported")
              ),
          },
          { id: "current", header: d("isCurrent"), cell: (row) => bool(row.isCurrent) },
          {
            id: "fieldCount",
            header: d("changedFieldCount"),
            cell: (row) => text(row.changedFieldCount),
          },
          {
            id: "fields",
            header: d("changedFields"),
            cellClassName: "max-w-64",
            cell: (row) => (
              <span className="block truncate">{row.changedFields.join(", ") || "—"}</span>
            ),
          },
          { id: "omitted", header: d("fieldsOmitted"), cell: (row) => text(row.fieldsOmitted) },
        ]
      : [
          {
            id: "name",
            header: t("name"),
            cellClassName: "max-w-52",
            cell: (row) => <span className="block truncate">{text(row.name)}</span>,
          },
          {
            id: "identity",
            header: t("identity"),
            cellClassName: "max-w-64",
            cell: (row) => <code className="block truncate">{text(row.nativeId)}</code>,
          },
          { id: "reachable", header: d("reachable"), cell: (row) => bool(row.reachable) },
          { id: "svid", header: d("hasValidSvid"), cell: (row) => bool(row.hasValidSvid) },
          {
            id: "server",
            header: t("spireAddress"),
            cellClassName: "max-w-52",
            cell: (row) => <span className="block truncate">{text(row.serverAddress)}</span>,
          },
          { id: "port", header: t("spirePort"), cell: (row) => text(row.serverPort) },
          {
            id: "expiry",
            header: d("svidExpiresAt"),
            cellClassName: "max-w-52",
            cell: (row) =>
              row.svidExpiresAt ? (
                <time dateTime={row.svidExpiresAt}>{row.svidExpiresAt}</time>
              ) : (
                d("notReported")
              ),
          },
        ];
  return (
    <Section title={d("title")} description={d("help")}>
      <Button variant="outline" onClick={props.onRefresh} disabled={props.loading}>
        {t("refresh")}
      </Button>
      {props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {props.error}
        </p>
      )}
      {props.loading && !summary && <Skeleton className="h-32" />}
      {observation && summary && (
        <>
          <p role="note" className="text-muted-foreground text-sm">
            {t("historicalHelp")}
          </p>
          <DefinitionList
            items={[
              { term: d("kind"), description: d(summary.kind) },
              {
                term: d("clusterId"),
                description: (
                  <code className="text-xs [overflow-wrap:anywhere]">{summary.clusterId}</code>
                ),
              },
              {
                term: t("observedAt"),
                description: observation.observedAt ? (
                  <time dateTime={observation.observedAt}>{observation.observedAt}</time>
                ) : (
                  d("notReported")
                ),
              },
              ...(summary.kind === "control_plane"
                ? [
                    {
                      term: d("configurationId"),
                      description: (
                        <code className="text-xs [overflow-wrap:anywhere]">
                          {text(summary.configurationId)}
                        </code>
                      ),
                    },
                    { term: t("version"), description: text(summary.kubernetesVersion) },
                    ...summary.counts.map((entry) => ({
                      term: entry.name === "desiredCount" ? t("desiredCount") : d(entry.name),
                      description: text(entry.value),
                    })),
                    ...summary.flags.map((entry) => ({
                      term: d(entry.name),
                      description: bool(entry.value),
                    })),
                    ...summary.conditions.map((entry) => ({
                      term: d(entry.name),
                      description: text(entry.value),
                    })),
                  ]
                : summary.kind === "spire"
                  ? [
                      {
                        term: t("spireAddress"),
                        description: (
                          <span className="[overflow-wrap:anywhere]">
                            {text(summary.requestedAddress)}
                          </span>
                        ),
                      },
                      { term: t("spirePort"), description: text(summary.requestedPort) },
                    ]
                  : []),
            ]}
          />
          {summary.kind !== "control_plane" && (
            <ListPage
              embedded
              list={list}
              label={d("title")}
              rows={table.rows}
              columns={columns}
              getRowId={(row) => row.key}
              loading={table.state === "loading"}
              stale={table.isStale}
              error={table.state === "error" ? { message: t("readFailed") } : null}
              onRetry={table.retry}
              nextCursor={table.nextCursor}
              totalCount={table.totalCount}
              empty={{ icon: <ActivityIcon />, title: d("empty"), description: d("help") }}
            />
          )}
        </>
      )}
    </Section>
  );
}
