"use client";

import { LockIcon, ShieldCheckIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { DetailPageTabs } from "@/components/DetailPageTabs";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { QueryError } from "@/components/QueryError";
import { Skeleton } from "@/components/ui/skeleton";

import { ZentinelleAudit, type ZentinelleAuditProps } from "./ZentinelleAudit";
import { type ZentinelleClusterActions, ZentinelleClusters } from "./ZentinelleClusters";
import {
  ZentinelleConnectForm,
  type ZentinelleConnectFormProps,
  ZentinelleConnectionCard,
  type ZentinelleConnectionCardProps,
} from "./ZentinelleConnection";
import { type ZentinelleHealth, ZentinelleOverview } from "./ZentinelleOverview";
import { ZentinellePolicies, type ZentinellePoliciesProps } from "./ZentinellePolicies";
import { ZentinelleUsage, type ZentinelleUsageProps } from "./ZentinelleUsage";
import {
  hasAnyAccess,
  visibleTabs,
  type ZentinelleAccess,
  type ZentinelleConnection,
  type ZentinelleTab,
} from "./zentinelle-model";

export interface ZentinelleScreenProps {
  /** Permissions or the connection are still loading. */
  loading: boolean;
  /** The connection read failed. */
  error: string | null;
  onRetry?: () => void;
  access: ZentinelleAccess;
  /** The organization is connected and Zentinelle still accepts it. */
  connected: boolean;
  /** Null when not connected, or when the viewer cannot read it. */
  connection: ZentinelleConnection | null;
  tab: ZentinelleTab;
  tabHref: (tab: ZentinelleTab) => string;
  connect: ZentinelleConnectFormProps;
  manage: Omit<ZentinelleConnectionCardProps, "connection" | "canConnect">;
  health: ZentinelleHealth;
  clusters: Omit<ZentinelleClusterActions, "canManage" | "canRotate">;
  usage: ZentinelleUsageProps;
  policies: ZentinellePoliciesProps;
  audit: Omit<ZentinelleAuditProps, "canExport">;
}

/**
 * Secure › Zentinelle (#1888): connect the organization, then its health,
 * cluster gateways, model usage, policies in effect, and audit, one tab
 * each. A tab shows only with the zentinelle.* grant that reads it; the
 * server enforces every one regardless.
 */
export function ZentinelleScreen(props: ZentinelleScreenProps) {
  const t = useTranslations("zentinelleScreen");
  const { access, connected, connection, loading, error } = props;
  const tabs = visibleTabs(access, connected);
  const tab = tabs.includes(props.tab) ? props.tab : tabs[0];

  let body: React.ReactNode;
  if (loading) {
    body = <Skeleton className="h-48 w-full" />;
  } else if (!hasAnyAccess(access) || (connected && tabs.length === 0)) {
    body = <EmptyState icon={<LockIcon />} title={t("noAccess")} description={t("noAccessBody")} />;
  } else if (error) {
    body = <QueryError title={t("error")} error={error} onRetry={props.onRetry} />;
  } else if (!connected) {
    body = (
      <div className="flex flex-col gap-4">
        {connection && (
          <ZentinelleConnectionCard
            {...props.manage}
            connection={connection}
            canConnect={access.connect}
          />
        )}
        {access.connect && !connection ? (
          <ZentinelleConnectForm {...props.connect} />
        ) : (
          !connection && (
            <EmptyState
              icon={<ShieldCheckIcon />}
              title={t("notConnected")}
              description={t("notConnectedBody")}
            />
          )
        )}
      </div>
    );
  } else {
    body = (
      <div className="flex min-w-0 flex-col gap-6">
        <DetailPageTabs
          ariaLabel={t("tabsLabel")}
          tabs={tabs.map((key) => ({
            key,
            label: t(`tabs.${key}`),
            href: props.tabHref(key),
            active: key === tab,
          }))}
        />
        {tab === "overview" && (
          <>
            <ZentinelleOverview connection={connection} {...props.health} />
            {connection && (
              <ZentinelleConnectionCard
                {...props.manage}
                connection={connection}
                canConnect={access.connect}
              />
            )}
          </>
        )}
        {tab === "clusters" && connection && (
          <ZentinelleClusters
            {...props.clusters}
            connection={connection}
            canManage={access.gatewayManage}
            canRotate={access.connect}
          />
        )}
        {tab === "usage" && <ZentinelleUsage {...props.usage} />}
        {tab === "policies" && <ZentinellePolicies {...props.policies} />}
        {tab === "audit" && <ZentinelleAudit {...props.audit} canExport={access.auditExport} />}
      </div>
    );
  }

  return (
    <PageShell title={t("title")} description={t("description")}>
      {body}
    </PageShell>
  );
}
