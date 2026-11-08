"use client";
import { useTranslations } from "next-intl";
import { ClipboardListIcon } from "lucide-react";
import type { Column, CursorTableController } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Section } from "@/components/ui/section";
import {
  installReviewSchema,
  installWorkloadId,
  type NvkeInstallReview,
  type NvkeInstallWorkload,
} from "@/lib/nvke/operator-install-review";
import type { NvkeOperatorAgentAuthority } from "./NvkeOperatorAgentPanel";
import type { NvkeSiteView, NvkeOperationView } from "./types";
export interface NvkeOperatorInstallReviewPanelProps {
  scopeKey: string;
  site: Pick<NvkeSiteView, "id" | "name" | "version" | "upstreamRevision">;
  operation: NvkeOperationView;
  authority: NvkeOperatorAgentAuthority | null;
  review: NvkeInstallReview | null;
  table: CursorTableController<NvkeInstallWorkload>;
  loading: boolean;
  pending: boolean;
  canReview: boolean;
  error: string | null;
  onReview: () => Promise<boolean>;
  onRefresh: () => void;
}
export function NvkeOperatorInstallReviewPanel(props: NvkeOperatorInstallReviewPanelProps) {
  const t = useTranslations("nvke"),
    j = useTranslations("nvke.installReview");
  const { authority, operation, site, table } = props;
  const sourceReady =
    props.canReview &&
    !!props.scopeKey &&
    operation.siteId === site.id &&
    operation.operationKey === "tooling.management.install" &&
    operation.status === "UNCERTAIN" &&
    !!authority &&
    authority.siteId === site.id &&
    authority.siteVersion === site.version &&
    authority.isEnabled &&
    authority.deletedAt === null &&
    authority.hasCredentials &&
    authority.verificationCode === "AGENT_VERIFIED" &&
    typeof authority.verifiedAt === "string" &&
    Number.isFinite(Date.parse(authority.verifiedAt));
  const parsed = installReviewSchema.safeParse(props.review);
  const candidate = parsed.success ? parsed.data : null;
  const matches =
    !!candidate &&
    !!authority &&
    candidate.siteId === site.id &&
    candidate.siteVersion === site.version &&
    candidate.operationId === operation.id &&
    candidate.operationVersion === operation.version &&
    candidate.authorityId === authority.id &&
    candidate.authorityVersion === authority.version &&
    candidate.instanceId === authority.instanceId &&
    candidate.bundleManifestSha256 === authority.bundleManifestSha256 &&
    candidate.nativeRevision === site.upstreamRevision;
  const review =
    sourceReady && matches && !props.loading && !props.error && candidate ? candidate : null;
  const list = useCursorTableList(table, {
    id: "nvke.operator.install.workloads",
    label: j("workloads"),
    searchPlaceholder: j("search"),
  });
  const count = (value: string | null) => <code>{value ?? j("unknown")}</code>;
  const columns: Column<NvkeInstallWorkload>[] = [
    { id: "namespace", header: t("providers.namespace"), cell: (row) => row.namespace },
    { id: "kind", header: j("kind"), cell: (row) => row.kind },
    {
      id: "name",
      header: t("name"),
      cellClassName: "max-w-64",
      cell: (row) => (
        <code className="block truncate" title={row.name}>
          {row.name}
        </code>
      ),
    },
    {
      id: "observation",
      header: j("observation"),
      cell: (row) => (
        <span className={row.observation === "ready" ? "text-success-fg" : "text-warning-fg"}>
          {j(row.observation === "ready" ? "ready" : "requiresReview")}
        </span>
      ),
    },
    { id: "generation", header: j("generation"), cell: (row) => count(row.generation) },
    {
      id: "observedGeneration",
      header: j("observedGeneration"),
      cell: (row) => count(row.observedGeneration),
    },
    { id: "desiredReplicas", header: j("desired"), cell: (row) => count(row.desiredReplicas) },
    { id: "readyReplicas", header: j("readyCount"), cell: (row) => count(row.readyReplicas) },
    { id: "updatedReplicas", header: j("updated"), cell: (row) => count(row.updatedReplicas) },
  ];
  return (
    <Section title={j("title")} description={j("help")}>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={!sourceReady || props.pending || props.loading}
          onClick={() => {
            void props.onReview();
          }}
        >
          {j("prepare")}
        </Button>
        <Button
          variant="outline"
          disabled={props.pending || props.loading}
          onClick={props.onRefresh}
        >
          {t("refresh")}
        </Button>
      </div>
      <p className="text-warning-fg text-sm">{j("originalUncertain")}</p>
      {props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {props.error}
        </p>
      )}
      {!review && !props.error && (
        <p role="status" className="text-muted-foreground text-sm">
          {props.loading ? j("loading") : props.review ? j("unavailable") : j("unloaded")}
        </p>
      )}
      {review && (
        <>
          <DefinitionList
            items={[
              {
                term: t("operatorJob.exitCode"),
                description: <code>{review.nativeExitCode}</code>,
              },
              { term: j("apiReady"), description: t("providers.yes") },
              {
                term: j("observedAt"),
                description: <time dateTime={review.observedAt}>{review.observedAt}</time>,
              },
              {
                term: j("expiresAt"),
                description: (
                  <time dateTime={review.snapshotExpiresAt}>{review.snapshotExpiresAt}</time>
                ),
              },
              {
                term: j("nativeRun"),
                description: (
                  <code className="text-xs [overflow-wrap:anywhere]">{review.nativeRunId}</code>
                ),
              },
              {
                term: j("digest"),
                description: (
                  <code className="text-xs [overflow-wrap:anywhere]">{review.reviewDigest}</code>
                ),
              },
            ]}
          />
          <ListPage
            embedded
            list={list}
            rows={table.state === "error" ? [] : table.rows}
            label={j("workloads")}
            loading={table.state === "loading"}
            stale={table.isStale}
            error={table.state === "error" ? { message: j("pageFailed") } : null}
            onRetry={table.retry}
            nextCursor={table.nextCursor}
            totalCount={table.totalCount}
            columns={columns}
            getRowId={installWorkloadId}
            empty={{ icon: <ClipboardListIcon />, title: j("empty") }}
          />
        </>
      )}
    </Section>
  );
}
