"use client";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { ModelSubscriptionsPanel } from "./ModelSubscriptionsPanel";
import { useModelSubscriptions } from "./use-model-subscriptions";
export function ModelSubscriptionsClient(props: {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefreshDeployment: () => void;
}) {
  return (
    <Context
      key={`${props.model.organizationId}:${props.model.id}:${props.model.clusterId}:${props.model.providerId}`}
      {...props}
    />
  );
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
  return (
    <ModelSubscriptionsPanel {...useModelSubscriptions(model, blocked, onRefreshDeployment)} />
  );
}
