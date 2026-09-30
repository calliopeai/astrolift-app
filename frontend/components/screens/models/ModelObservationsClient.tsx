"use client";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { ModelObservationsPanel } from "./ModelObservationsPanel";
import { useModelObservations } from "./use-model-observations";
export function ModelObservationsClient({ model }: { model: ClusterModelFieldsFragment }) {
  return (
    <ObservationContext
      key={`${model.organizationId}:${model.id}:${model.version}:${model.clusterId}:${model.providerId}`}
      model={model}
    />
  );
}
function ObservationContext({ model }: { model: ClusterModelFieldsFragment }) {
  return <ModelObservationsPanel {...useModelObservations(model)} />;
}
