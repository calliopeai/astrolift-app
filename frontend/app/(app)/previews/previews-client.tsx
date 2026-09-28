"use client";

import { PreviewsScreen } from "@/components/screens/previews/PreviewsScreen";
import { usePreviews } from "@/components/screens/previews/use-previews";

export function PreviewsClient() {
  return <PreviewsScreen {...usePreviews()} />;
}
