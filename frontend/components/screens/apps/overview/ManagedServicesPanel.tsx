"use client";

import { PlugIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { ListSummary } from "@/components/list/ListSummary";
import type { PanelSpan } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";

import type { SummaryService, useManagedServicesSummary } from "./use-managed-services-summary";

export type ManagedServicesPanelProps = Pick<
  ReturnType<typeof useManagedServicesSummary>,
  "loading" | "services"
> &
  Partial<Pick<ReturnType<typeof useManagedServicesSummary>, "error" | "onRetry">> & {
    /** The Workloads tab's managed-services section. */
    managedServicesHref: string;
    span?: PanelSpan;
  };

const DOT: Record<string, "ok" | "warn" | "error" | "pending" | "muted"> = {
  active: "ok",
  failed: "error",
  pending: "pending",
  provisioning: "pending",
  updating: "pending",
  deprovisioning: "warn",
};

/**
 * The overview's managed-services summary (Leo's list rule 3): a count and
 * the top bound services with their state, failed first (the hook sorts),
 * then "View all" to the Workloads tab's managed-services section, where
 * connection details, test email and the rest live. Reading only.
 */
export function ManagedServicesPanel({
  loading,
  services,
  managedServicesHref,
  error,
  onRetry,
  span = 6,
}: ManagedServicesPanelProps) {
  const t = useTranslations("apps.overview.managedServices");
  const live = services.filter((s) => s.status !== "deleted");
  return (
    <ListSummary
      title={t("title")}
      icon={<PlugIcon className="size-4" />}
      span={span}
      count={loading && live.length === 0 ? null : live.length}
      rows={live}
      keyOf={(svc) => svc.id}
      renderRow={(svc) => <ServiceRow svc={svc} />}
      viewAllHref={managedServicesHref}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <PlugIcon className="size-5" />,
        title: t("emptyTitle"),
        description: t("emptyDescription"),
        actionHref: managedServicesHref,
        actionLabel: t("emptyAction"),
      }}
    />
  );
}

function ServiceRow({ svc }: { svc: SummaryService }) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      <StatusDot status={DOT[svc.status] ?? "muted"} className="mt-1.5" />
      <div className="min-w-0 flex-1">
        <p className="font-mono text-sm [overflow-wrap:anywhere]">{svc.name || svc.kind}</p>
        <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
          <span className="font-mono">
            {svc.variant ? `${svc.kind} · ${svc.variant}` : svc.kind}
          </span>
          {svc.environmentName && <> · {svc.environmentName}</>}
          {" · "}
          {svc.status}
        </p>
        {svc.status === "failed" && svc.statusError && (
          <p className="text-danger-fg mt-0.5 font-mono text-xs [overflow-wrap:anywhere]">
            {svc.statusError}
          </p>
        )}
      </div>
    </div>
  );
}
