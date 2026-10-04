"use client";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { SharedModelManagementPanel } from "./SharedModelManagementPanel";
import { useSharedModelManagement } from "./use-shared-model-management";
import { useMe } from "@/graphql/user/user.hooks";
export function SharedModelManagementClient(props: {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  onRefresh: () => void;
}) {
  const { user } = useMe();
  return (
    <Context
      key={`${user?.id ?? ""}:${props.model.organizationId}:${props.model.id}:${props.model.clusterId}:${props.model.providerId}`}
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
