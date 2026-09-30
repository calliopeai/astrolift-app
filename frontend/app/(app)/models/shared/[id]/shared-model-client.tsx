"use client";
import { SharedModelDetailScreen } from "@/components/screens/models/SharedModelDetailScreen";
import { useSharedModelDetail } from "@/components/screens/models/use-shared-model-detail";
import { ModelObservationsClient } from "@/components/screens/models/ModelObservationsClient";
export function SharedModelClient({ id }: { id: string }) {
  const props = useSharedModelDetail(id);
  return (
    <SharedModelDetailScreen
      {...props}
      observations={props.model ? <ModelObservationsClient model={props.model} /> : null}
    />
  );
}
