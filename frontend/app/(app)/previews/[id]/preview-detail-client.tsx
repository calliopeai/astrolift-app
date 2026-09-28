"use client";

import { PreviewDetailScreen } from "@/components/screens/previews/PreviewDetail";
import { usePreviewDetail } from "@/components/screens/previews/use-preview-detail";

/**
 * Preview environment detail (#1106). Reuses the global LIST_PREVIEW_ENVIRONMENTS
 * query (no singular query exists) — a cache hit when navigated from /previews.
 */
export function PreviewDetailClient({ id }: { id: string }) {
  return <PreviewDetailScreen {...usePreviewDetail(id)} />;
}
