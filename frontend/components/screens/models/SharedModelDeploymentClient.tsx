"use client";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { SharedModelDeploymentScreen } from "./SharedModelDeploymentScreen";
import { HuggingFaceCataloguePanel } from "./HuggingFaceCataloguePanel";
import { useSharedModelDeployment } from "./use-shared-model-deployment";
export function SharedModelDeploymentClient() {
  const { org } = useActiveOrg();
  return <PlacementContext key={org?.id ?? "no-organization"} />;
}
function PlacementContext() {
  const { catalogueProps, ...props } = useSharedModelDeployment();
  return (
    <SharedModelDeploymentScreen
      {...props}
      catalogue={<HuggingFaceCataloguePanel {...catalogueProps} />}
    />
  );
}
