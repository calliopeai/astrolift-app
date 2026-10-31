"use client";

import { KeyRoundIcon, PlusIcon, ServerIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";

import type { WriteResult } from "./ZentinelleConnection";
import {
  gatewayHealth,
  HEALTH_DOT,
  liveClusters,
  type ZentinelleCluster,
  type ZentinelleConnection,
} from "./zentinelle-model";

export interface ClusterChoice {
  id: string;
  slug: string;
  name: string;
  /** No organization: it registers through the install connection (#1905). */
  shared: boolean;
}

/** Shared clusters, which register through the install's connection, the
 * platform operator's (#1905). An organization's connection refuses them. */
export interface SharedClusterActions {
  /** The viewer is the platform operator, so the install connection is readable. */
  operator: boolean;
  /** The install connection and its clusters; null when not connected or not the operator's view. */
  connection: ZentinelleConnection | null;
  onRegister: (clusterId: string) => WriteResult;
  onUnregister: (clusterId: string) => WriteResult;
}

export interface ZentinelleClusterActions {
  /** zentinelle.gateway_manage: register, unregister, gateway on and off. */
  canManage: boolean;
  /** zentinelle.connect: rotate a gateway's credential. */
  canRotate: boolean;
  /** The cluster a write is in flight for. */
  busyClusterId: string | null;
  choices: { items: ClusterChoice[]; loading: boolean; search: string };
  onSearch: (search: string) => void;
  onRegister: (clusterId: string) => WriteResult;
  onSetEnabled: (clusterId: string, enabled: boolean) => WriteResult;
  onRotate: (clusterId: string) => WriteResult;
  onUnregister: (clusterId: string) => WriteResult;
  shared: SharedClusterActions;
}

export interface ZentinelleClustersProps extends ZentinelleClusterActions {
  connection: ZentinelleConnection;
}

/** Clusters registered with Zentinelle, their gateways, and registering more. */
export function ZentinelleClusters({
  connection,
  canManage,
  canRotate,
  busyClusterId,
  choices,
  onSearch,
  onRegister,
  onSetEnabled,
  onRotate,
  onUnregister,
  shared,
}: ZentinelleClustersProps) {
  const t = useTranslations("zentinelleScreen.clusters");
  const th = useTranslations("zentinelleScreen.health");
  const format = useFormatter();
  const [error, setError] = React.useState<string | null>(null);
  const [removing, setRemoving] = React.useState<{
    cluster: ZentinelleCluster;
    shared: boolean;
  } | null>(null);
  const clusters = liveClusters(connection);
  const sharedClusters = liveClusters(shared.connection);
  const registered = new Set([...clusters, ...sharedClusters].map((c) => c.clusterId));
  const unregistered = choices.items.filter((c) => !c.shared && !registered.has(c.id));
  const sharedChoices = choices.items.filter((c) => c.shared && !registered.has(c.id));
  const sharedConnected = shared.connection?.status === "connected";

  async function run(write: WriteResult) {
    setError(await write);
  }

  const when = (iso?: string | null) =>
    iso ? format.dateTime(new Date(iso), { dateStyle: "medium", timeStyle: "short" }) : t("never");

  return (
    <div className="flex min-w-0 flex-col gap-6">
      {!connection.gatewayFeatureEnabled && (
        <p className="text-muted-foreground text-sm">{t("installOff")}</p>
      )}
      {error && (
        <p role="alert" className="text-danger-fg text-sm [overflow-wrap:anywhere]">
          {error}
        </p>
      )}
      <Section title={t("title")}>
        <Card>
          <CardContent className="flex min-w-0 flex-col">
            {clusters.length === 0 ? (
              <p className="text-muted-foreground py-6 text-center text-sm">{t("empty")}</p>
            ) : (
              clusters.map((cluster) => {
                const health = gatewayHealth(cluster, connection.gatewayFeatureEnabled);
                const busy = busyClusterId === cluster.clusterId;
                return (
                  <div
                    key={cluster.clusterId}
                    className="border-border/60 flex min-w-0 flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <StatusDot status={HEALTH_DOT[health]} />
                        <span className="font-mono text-sm [overflow-wrap:anywhere]">
                          {cluster.clusterSlug}
                        </span>
                        <span className="text-muted-foreground text-xs">{th(health)}</span>
                      </div>
                      <div className="text-muted-foreground mt-1 text-xs">
                        {t("registeredAt", { when: when(cluster.registeredAt) })}
                        {" · "}
                        {t("rotatedAt", { when: when(cluster.credentialRotatedAt) })}
                      </div>
                      {cluster.lastError && (
                        <p className="text-danger-fg mt-1 text-xs [overflow-wrap:anywhere]">
                          {cluster.lastError}
                        </p>
                      )}
                    </div>
                    {canManage && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() =>
                          run(onSetEnabled(cluster.clusterId, !cluster.gatewayEnabled))
                        }
                      >
                        {cluster.gatewayEnabled ? t("turnOff") : t("turnOn")}
                      </Button>
                    )}
                    {canRotate && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => run(onRotate(cluster.clusterId))}
                      >
                        <KeyRoundIcon className="mr-1 size-4" />
                        {t("rotate")}
                      </Button>
                    )}
                    {canManage && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-danger-fg"
                        disabled={busy}
                        onClick={() => setRemoving({ cluster, shared: false })}
                      >
                        {t("unregister")}
                      </Button>
                    )}
                  </div>
                );
              })
            )}
          </CardContent>
        </Card>
      </Section>

      {canManage && (
        <Section title={t("registerTitle")} description={t("registerDescription")}>
          <Card>
            <CardContent className="flex min-w-0 flex-col gap-3">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  onSearch(String(new FormData(e.currentTarget).get("search") ?? "").trim());
                }}
              >
                <Input
                  name="search"
                  defaultValue={choices.search}
                  aria-label={t("search")}
                  placeholder={t("search")}
                  className="max-w-sm"
                />
              </form>
              {choices.loading ? (
                <Skeleton className="h-10 w-full" />
              ) : unregistered.length === 0 ? (
                <p className="text-muted-foreground py-3 text-sm">{t("noChoices")}</p>
              ) : (
                unregistered.map((choice) => (
                  <div
                    key={choice.id}
                    className="border-border/60 flex min-w-0 items-center gap-3 border-b py-2 last:border-b-0"
                  >
                    <ServerIcon className="text-muted-foreground size-4 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium [overflow-wrap:anywhere]">
                        {choice.name}
                      </div>
                      <div className="text-muted-foreground font-mono text-xs">{choice.slug}</div>
                    </div>
                    <Button
                      size="sm"
                      disabled={busyClusterId === choice.id}
                      onClick={() => run(onRegister(choice.id))}
                    >
                      <PlusIcon className="mr-1 size-4" />
                      {t("register")}
                    </Button>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </Section>
      )}

      {canManage && (shared.operator || sharedChoices.length > 0) && (
        <Section title={t("sharedTitle")} description={t("sharedDescription")}>
          <Card>
            <CardContent className="flex min-w-0 flex-col">
              {shared.operator && !sharedConnected && (
                <p className="text-muted-foreground py-3 text-sm">{t("sharedNotConnected")}</p>
              )}
              {shared.operator &&
                sharedClusters.map((cluster) => {
                  const health = gatewayHealth(cluster, connection.gatewayFeatureEnabled);
                  return (
                    <div
                      key={cluster.clusterId}
                      className="border-border/60 flex min-w-0 flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <StatusDot status={HEALTH_DOT[health]} />
                          <span className="font-mono text-sm [overflow-wrap:anywhere]">
                            {cluster.clusterSlug}
                          </span>
                          <span className="text-muted-foreground text-xs">{th(health)}</span>
                        </div>
                        <div className="text-muted-foreground mt-1 text-xs">
                          {t("registeredAt", { when: when(cluster.registeredAt) })}
                        </div>
                        {cluster.lastError && (
                          <p className="text-danger-fg mt-1 text-xs [overflow-wrap:anywhere]">
                            {cluster.lastError}
                          </p>
                        )}
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-danger-fg"
                        disabled={busyClusterId === cluster.clusterId}
                        onClick={() => setRemoving({ cluster, shared: true })}
                      >
                        {t("unregister")}
                      </Button>
                    </div>
                  );
                })}
              {sharedChoices.map((choice) => (
                <div
                  key={choice.id}
                  className="border-border/60 flex min-w-0 items-center gap-3 border-b py-2 last:border-b-0"
                >
                  <ServerIcon className="text-muted-foreground size-4 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium [overflow-wrap:anywhere]">
                      {choice.name}
                    </div>
                    <div className="text-muted-foreground font-mono text-xs">{choice.slug}</div>
                  </div>
                  {shared.operator && sharedConnected ? (
                    <Button
                      size="sm"
                      disabled={busyClusterId === choice.id}
                      onClick={() => run(shared.onRegister(choice.id))}
                    >
                      <PlusIcon className="mr-1 size-4" />
                      {t("register")}
                    </Button>
                  ) : (
                    !shared.operator && (
                      <span className="text-muted-foreground text-xs">{t("sharedManaged")}</span>
                    )
                  )}
                </div>
              ))}
              {shared.operator &&
                sharedConnected &&
                sharedClusters.length === 0 &&
                sharedChoices.length === 0 && (
                  <p className="text-muted-foreground py-3 text-sm">{t("sharedEmpty")}</p>
                )}
            </CardContent>
          </Card>
        </Section>
      )}

      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t("unregisterTitle", { cluster: removing?.cluster.clusterSlug ?? "" })}
        description={t("unregisterBody")}
        confirmLabel={t("unregister")}
        destructive
        onConfirm={async () => {
          if (!removing) return;
          const id = removing.cluster.clusterId;
          await run(removing.shared ? shared.onUnregister(id) : onUnregister(id));
        }}
      />
    </div>
  );
}
