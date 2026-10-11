"use client";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useModelSubscriptionUsage } from "@/graphql/models/subscription-usage.hooks";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import type { ModelSubscription } from "./ModelSubscriptionsPanel";
import { ModelSubscriptionUsagePanel } from "./ModelSubscriptionUsagePanel";
type Props = {
  model: ClusterModelFieldsFragment;
  subscription: ModelSubscription;
  onClose: () => void;
};
export function ModelSubscriptionUsageClient(props: Props) {
  const { org } = useActiveOrg(),
    { user } = useMe();
  return (
    <Context
      key={`${user?.id ?? ""}:${org?.id ?? ""}:${props.model.organizationId}:${props.model.id}:${props.model.version}:${props.model.clusterId}:${props.model.providerId}:${props.subscription.id}:${props.subscription.version}`}
      {...props}
    />
  );
}
function Context({ model, subscription, onClose }: Props) {
  const props = useModelSubscriptionUsage(model, subscription.id);
  return (
    <ModelSubscriptionUsagePanel
      {...props}
      appSlug={subscription.appSlug}
      environmentName={subscription.environmentName}
      onClose={onClose}
    />
  );
}
