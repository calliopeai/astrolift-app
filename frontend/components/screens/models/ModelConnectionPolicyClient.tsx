"use client";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { ModelConnectionPolicyPanel } from "./ModelConnectionPolicyPanel";
import { useModelConnectionPolicy } from "./use-model-connection-policy";
export function ModelConnectionPolicyClient({
  model = null,
  blocked = false,
}: {
  model?: ClusterModelFieldsFragment | null;
  blocked?: boolean;
}) {
  const { org } = useActiveOrg(),
    { user } = useMe();
  return (
    <Context
      key={`${user?.id}:${org?.id}:${model?.id}:${model?.clusterId}:${model?.providerId}`}
      model={model}
      blocked={blocked}
    />
  );
}
function Context({
  model,
  blocked,
}: {
  model: ClusterModelFieldsFragment | null;
  blocked: boolean;
}) {
  return <ModelConnectionPolicyPanel {...useModelConnectionPolicy(model, blocked)} />;
}
