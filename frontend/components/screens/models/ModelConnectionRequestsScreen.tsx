"use client";

import Link from "next/link";
import { LinkIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { ListPage } from "@/components/list/ListPage";
import { Button } from "@/components/ui/button";
import type { ModelConnectionRequestFieldsFragment } from "@/graphql/__generated__/operations";
import type { ModelPage } from "./ModelSubscriptionsPanel";

export type ModelConnectionRequestsProps = {
  review: boolean;
  onReview: (review: boolean) => void;
  supported: boolean | null;
  supportError: string | null;
  onRetrySupport: () => void;
  page: ModelPage<ModelConnectionRequestFieldsFragment>;
};
export function connectionStatus(
  status: string
): "pending" | "approved" | "rejected" | "cancelled" | "stale" | null {
  switch (status) {
    case "PENDING":
      return "pending";
    case "APPROVED":
      return "approved";
    case "REJECTED":
      return "rejected";
    case "CANCELLED":
      return "cancelled";
    case "STALE":
      return "stale";
    default:
      return null;
  }
}
export function ModelConnectionRequestsScreen(props: ModelConnectionRequestsProps) {
  const t = useTranslations("models.shared.connections"),
    format = useFormatter();
  const controls = (
    <div className="flex flex-wrap gap-2">
      <Button variant={!props.review ? "default" : "outline"} onClick={() => props.onReview(false)}>
        {t("myRequests")}
      </Button>
      <Button variant={props.review ? "default" : "outline"} onClick={() => props.onReview(true)}>
        {t("reviewInbox")}
      </Button>
      <Button variant="outline" asChild>
        <Link href="/models">{t("model")}</Link>
      </Button>
    </div>
  );
  if (props.supported !== true)
    return (
      <section className="space-y-4">
        <h1>{t("title")}</h1>
        {controls}
        <p role={props.supportError ? "alert" : "status"}>
          {props.supportError ??
            t(props.supported === false ? "unsupported" : "capabilityChecking")}
        </p>
        <Button onClick={props.onRetrySupport}>{t("retry")}</Button>
      </section>
    );
  return (
    <ListPage
      {...props.page}
      header={{
        crumbs: [{ label: t("model"), href: "/models" }, { label: t("title") }],
        title: t(props.review ? "reviewInbox" : "myRequests"),
        context: t(props.review ? "inboxDescription" : "requestsDescription"),
        primaryAction: controls,
      }}
      label={t("title")}
      getRowId={(row) => row.id}
      rowHref={(row) =>
        `/models/connections/${encodeURIComponent(row.id)}?version=${row.version}${props.review ? "&review=1" : ""}`
      }
      empty={{
        icon: <LinkIcon />,
        title: t("emptyRequests"),
        description: t("emptyRequestsDescription"),
      }}
      columns={[
        { id: "model", header: t("model"), cell: (row) => row.modelName ?? row.modelDeploymentId },
        {
          id: "destination",
          header: t("destination"),
          cell: (row) =>
            `${row.appName ?? row.appId} / ${row.environmentName ?? row.appEnvironmentId}`,
        },
        { id: "alias", header: t("alias"), cell: (row) => row.alias },
        {
          id: "requester",
          header: t("requester"),
          cell: (row) => row.requesterUsername ?? t("unknown"),
        },
        {
          id: "status",
          header: t("status"),
          cell: (row) =>
            connectionStatus(row.status) ? t(connectionStatus(row.status)!) : row.status,
        },
        {
          id: "votes",
          header: t("approval"),
          cell: (row) => t("votes", { count: row.approvalCount, required: row.requiredApprovals }),
        },
        {
          id: "created",
          header: t("createdAt"),
          cell: (row) =>
            row.createdAt && Number.isFinite(Date.parse(row.createdAt))
              ? format.dateTime(new Date(row.createdAt), {
                  dateStyle: "medium",
                  timeStyle: "short",
                })
              : t("unknown"),
        },
      ]}
    />
  );
}
