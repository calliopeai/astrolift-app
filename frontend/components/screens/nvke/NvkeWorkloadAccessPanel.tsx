"use client";

import { useState } from "react";
import Link from "next/link";
import { KeyIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column, CursorTableController } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Section } from "@/components/ui/section";
import type { ListNvkeAccessQuery } from "@/graphql/__generated__/operations";
import type { NvkeSiteView } from "./types";

export type NvkeAccessBinding =
  ListNvkeAccessQuery["astroliftNvkeWorkloadAccessBindings"]["items"][number];
export interface NvkeAccessEnrollment {
  sourceOperationId: string;
  sourceVersion: number;
  clusterId: string;
  siteVersion: number;
}
export interface NvkeAccessRevocation {
  id: string;
  version: number;
  clusterId: string;
  siteVersion: number;
}
export interface NvkeWorkloadAccessPanelProps {
  site: NvkeSiteView;
  table: CursorTableController<NvkeAccessBinding>;
  candidate: NvkeAccessEnrollment | null;
  candidateLoading: boolean;
  candidateFailed: boolean;
  canManage: boolean;
  pending: boolean;
  error: string | null;
  enrolled: boolean;
  onRefresh: () => void;
  onEnroll: (review: NvkeAccessEnrollment) => Promise<boolean>;
  onRevoke: (review: NvkeAccessRevocation) => Promise<boolean>;
}
export function NvkeWorkloadAccessPanel(props: NvkeWorkloadAccessPanelProps) {
  const t = useTranslations("nvke.workloadAccess"),
    common = useTranslations("nvke");
  const { site, table, candidate } = props;
  const [review, setReview] = useState<
    | { kind: "enroll"; value: NvkeAccessEnrollment }
    | { kind: "revoke"; value: NvkeAccessRevocation }
    | null
  >(null);
  const list = useCursorTableList(table, {
    id: "nvke.workload-access",
    label: t("title"),
    searchPlaceholder: "",
    searchable: false,
  });
  const allowed = props.canManage && !props.pending && !props.error;
  const same =
    review?.value.siteVersion === site.version &&
    (review?.kind === "enroll"
      ? !!candidate && JSON.stringify(review.value) === JSON.stringify(candidate)
      : !!review &&
        table.rows.some(
          (row) =>
            row.id === review.value.id &&
            row.version === review.value.version &&
            row.clusterId === review.value.clusterId
        ));
  const columns: Column<NvkeAccessBinding>[] = [
    {
      id: "cluster",
      header: t("cluster"),
      cellClassName: "max-w-72",
      cell: (row) => (
        <code className="block truncate" title={row.clusterId}>
          {row.clusterId}
        </code>
      ),
    },
    {
      id: "source",
      header: t("source"),
      cellClassName: "max-w-72",
      cell: (row) => (
        <Link
          className="block truncate hover:underline"
          href={`/nvke/${site.id}/operations/${row.sourceOperationId}`}
        >
          {row.sourceOperationId}
        </Link>
      ),
    },
    { id: "version", header: common("version"), cell: (row) => row.version },
    {
      id: "connection",
      header: t("connection"),
      cell: (row) =>
        t(
          site.connections.some(
            (c) => c.surface === "CONTROLLER" && c.isEnabled && c.id === row.controllerConnectionId
          ) && row.upstreamRevision === site.upstreamRevision
            ? "currentConnection"
            : "inactiveConnection"
        ),
    },
  ];
  return (
    <Section title={t("title")} description={t("help")}>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" disabled={props.pending} onClick={props.onRefresh}>
          {common("refresh")}
        </Button>
        {candidate && (
          <Button
            disabled={!allowed || props.enrolled}
            onClick={() => setReview({ kind: "enroll", value: { ...candidate } })}
          >
            {t(props.enrolled ? "enrolled" : "enroll")}
          </Button>
        )}
      </div>
      {props.candidateLoading && <p role="status">{t("preparing")}</p>}
      {props.candidateFailed && <p role="alert">{t("sourceUnavailable")}</p>}
      {props.error && <p role="alert">{props.error}</p>}
      <ListPage
        embedded
        list={list}
        rows={table.rows}
        label={t("title")}
        loading={table.state === "loading"}
        stale={table.isStale}
        error={table.state === "error" ? { message: common("readFailed") } : null}
        onRetry={table.retry}
        nextCursor={table.nextCursor}
        totalCount={table.totalCount}
        columns={columns}
        getRowId={(row) => row.id}
        rowActions={
          props.canManage
            ? (row) => (
                <DropdownMenuItem
                  disabled={!allowed}
                  onSelect={() =>
                    setReview({
                      kind: "revoke",
                      value: {
                        id: row.id,
                        version: row.version,
                        clusterId: row.clusterId,
                        siteVersion: site.version,
                      },
                    })
                  }
                >
                  {t("revoke")}
                </DropdownMenuItem>
              )
            : undefined
        }
        empty={{ icon: <KeyIcon />, title: t("empty"), description: t("emptyHelp") }}
      />
      <ConfirmDialog
        open={!!review}
        onOpenChange={(open) => {
          if (!open) setReview(null);
        }}
        title={t(review?.kind === "enroll" ? "enroll" : "revoke")}
        destructive={review?.kind === "revoke"}
        confirmDisabled={!allowed || !same}
        description={
          <span className="block space-y-2">
            <span className="block">
              {t(review?.kind === "enroll" ? "enrollHelp" : "revokeHelp")}
            </span>
            <span className="block break-words">
              {t("review", {
                site: site.name,
                siteVersion: review?.value.siteVersion ?? site.version,
                cluster: review?.value.clusterId ?? "",
              })}
            </span>
            <span className="block break-words">
              {review?.kind === "enroll"
                ? t("sourceReview", {
                    source: review.value.sourceOperationId,
                    version: review.value.sourceVersion,
                  })
                : review
                  ? t("bindingReview", { binding: review.value.id, version: review.value.version })
                  : ""}
            </span>
          </span>
        }
        onConfirm={async () => {
          if (!review || !allowed || !same) return false;
          const confirmed = await (review.kind === "enroll"
            ? props.onEnroll(review.value)
            : props.onRevoke(review.value));
          if (!confirmed) setReview(null);
          return confirmed;
        }}
      />
    </Section>
  );
}
