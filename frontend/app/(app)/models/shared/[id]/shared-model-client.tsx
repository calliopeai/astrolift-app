"use client";
import { SharedModelDetailScreen } from "@/components/screens/models/SharedModelDetailScreen";
import { useSharedModelDetail } from "@/components/screens/models/use-shared-model-detail";
export function SharedModelClient({ id }: { id: string }) {
  return <SharedModelDetailScreen {...useSharedModelDetail(id)} />;
}
