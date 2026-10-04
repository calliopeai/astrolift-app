"use client";
import { useState } from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { ModelSubscriptionUsageClient } from "./ModelSubscriptionUsageClient";
import type { ModelSubscription } from "./ModelSubscriptionsPanel";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { ModelSubscriptionsPanel } from "./ModelSubscriptionsPanel";
import { useModelSubscriptions } from "./use-model-subscriptions";
export function ModelSubscriptionsClient(props: {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefreshDeployment: () => void;
}) {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return <Context key={`${user?.id ?? ""}:${org?.id ?? ""}`} {...props} />;
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
  const props = useModelSubscriptions(model, blocked, onRefreshDeployment);
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
      usageBlocked={!pageReady}
      onSelectUsage={(row) => {
        if (
          pageReady &&
          props.subscriptions.rows.some(
            (candidate) => candidate.id === row.id && candidate.version === row.version
          )
        )
          setSelection({ row, pageKey });
      }}
      usage={
        selected ? (
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
