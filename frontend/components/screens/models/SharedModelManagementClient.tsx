"use client";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { SharedModelManagementPanel } from "./SharedModelManagementPanel";
import { useSharedModelManagement } from "./use-shared-model-management";
export function SharedModelManagementClient(props: {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefresh: () => void;
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
  onRefresh,
}: {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefresh: () => void;
}) {
  return <SharedModelManagementPanel {...useSharedModelManagement(model, blocked, onRefresh)} />;
}
