"use client";

import { PlugIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Panel, type PanelSpan } from "@/components/panel/Panel";
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
 * The overview's managed-services summary: each bound service with its
 * state, failed first (the hook sorts). Reading only; connection details,
 * test email and the rest live on the Workloads tab's managed-services
 * section, which the heading links to.
 */
export function ManagedServicesPanel({
  loading,
  services,
  managedServicesHref,
  error,
  onRetry,
  span = 4,
}: ManagedServicesPanelProps) {
  const t = useTranslations("apps.overview.managedServices");
  const live = services.filter((s) => s.status !== "deleted");
  return (
    <Panel
      title={t("title")}
      icon={<PlugIcon className="size-4" />}
      span={span}
      loading={loading}
      error={error}
      onRetry={onRetry}
      flush
      actions={
        live.length > 0 ? (
          <Link href={managedServicesHref} className="text-primary text-xs hover:underline">
            {t("manage")}
          </Link>
        ) : undefined
      }
      empty={
        live.length === 0
          ? {
              icon: <PlugIcon className="size-5" />,
              title: t("emptyTitle"),
              description: t("emptyDescription"),
              actionHref: managedServicesHref,
              actionLabel: t("emptyAction"),
            }
          : null
      }
    >
      <ul className="divide-y">
        {live.map((svc) => (
          <ServiceRow key={svc.id} svc={svc} />
        ))}
      </ul>
    </Panel>
  );
}

function ServiceRow({ svc }: { svc: SummaryService }) {
  return (
    <li className="flex min-w-0 items-start gap-3 px-4 py-2.5">
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
    </li>
  );
}
