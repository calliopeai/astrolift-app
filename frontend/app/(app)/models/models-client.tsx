"use client";
import { SharedModelsScreen } from "@/components/screens/models/SharedModelsScreen";
import { useSharedModels } from "@/components/screens/models/use-shared-models";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
export function ModelsClient() {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return <InventoryContext key={`${org?.id ?? ""}:${user?.id ?? ""}`} />;
}
function InventoryContext() {
  return <SharedModelsScreen {...useSharedModels()} />;
}
