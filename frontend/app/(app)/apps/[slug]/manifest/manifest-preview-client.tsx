"use client";

import { ManifestPreviewScreen } from "@/components/screens/apps/config/ManifestPreviewScreen";
import { useManifestPreview } from "@/components/screens/apps/config/use-manifest-preview";

import { AppTabs } from "../components/app-tabs";

export function ManifestPreviewClient({ slug }: { slug: string }) {
  return (
    <ManifestPreviewScreen
      {...useManifestPreview(slug)}
      slug={slug}
      tabs={<AppTabs slug={slug} active="settings" />}
    />
  );
}
