"use client";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { ModelConnectionIntakePanel } from "./ModelConnectionIntakePanel";
import { useModelConnectionIntake } from "./use-model-connection-intake";
import { useState } from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { ModelSubscriptionUsageClient } from "./ModelSubscriptionUsageClient";
import type { ModelSubscription } from "./ModelSubscriptionsPanel";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { ModelSubscriptionsPanel } from "./ModelSubscriptionsPanel";
import { useModelSubscriptions } from "./use-model-subscriptions";
import { modelSourceMode } from "./native-model-source";
export function ModelSubscriptionsClient(props: {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefreshDeployment: () => void;
}) {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return <Views key={`${user?.id ?? ""}:${org?.id ?? ""}:${props.model.id}`} {...props} />;
}
function Views(props: {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefreshDeployment: () => void;
}) {
  const t = useTranslations("models.shared.connections");
  const [adding, setAdding] = useState(false);
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => setAdding(!adding)}>
          {t(adding ? "connections" : "add")}
        </Button>
        <Button variant="outline" asChild>
          <Link href="/models/connections">{t("viewRequests")}</Link>
        </Button>
      </div>
      {adding ? <IntakeContext {...props} /> : <Context {...props} />}
    </section>
  );
}
function IntakeContext({
  model,
  blocked,
  onRefreshDeployment,
}: {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefreshDeployment: () => void;
}) {
  const props = useModelConnectionIntake(model, blocked, onRefreshDeployment);
  return <ModelConnectionIntakePanel {...props} />;
}
function Context({
  model,
  blocked,
  onRefreshDeployment,
}: {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefreshDeployment: () => void;
}) {
  const props = useModelSubscriptions(model, blocked, onRefreshDeployment, false);
  const pageKey = JSON.stringify([
    model.organizationId,
    model.id,
    model.version,
    model.clusterId,
    model.providerId,
    props.subscriptions.list.state,
  ]);
  const [selection, setSelection] = useState<{ row: ModelSubscription | null; pageKey: string }>({
    row: null,
    pageKey,
  });
  if (selection.pageKey !== pageKey) setSelection({ row: null, pageKey });
  const pageReady =
    !blocked &&
    !props.subscriptions.loading &&
    !props.subscriptions.stale &&
    !props.subscriptions.error;
  const trafficSupported = modelSourceMode(model) === "hosted";
  const selected =
    pageReady && selection.row && selection.pageKey === pageKey
      ? props.subscriptions.rows.find(
          (row) =>
            row.id === selection.row!.id &&
            row.version === selection.row!.version &&
            row.appSlug === selection.row!.appSlug &&
            row.environmentName === selection.row!.environmentName
        )
      : null;
  return (
    <ModelSubscriptionsPanel
      {...props}
      mode="connections"
      usageBlocked={!pageReady}
      onSelectUsage={
        trafficSupported
          ? (row) => {
              if (
                pageReady &&
                props.subscriptions.rows.some(
                  (candidate) => candidate.id === row.id && candidate.version === row.version
                )
              )
                setSelection({ row, pageKey });
            }
          : undefined
      }
      usage={
        selected && trafficSupported ? (
          <ModelSubscriptionUsageClient
            key={`${selected.id}:${selected.version}`}
            model={model}
            subscription={selected}
            onClose={() => setSelection({ row: null, pageKey })}
          />
        ) : null
      }
    />
  );
}
