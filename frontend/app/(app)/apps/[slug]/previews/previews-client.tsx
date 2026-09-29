"use client";

import { AppPreviewsScreen } from "@/components/screens/apps/security/AppPreviewsScreen";
import { useAppPreviews } from "@/components/screens/apps/security/use-app-previews";

import { appPath, useAppChrome } from "../components/app-chrome-context";

/** The Previews view of the Deployments tab (`?view=previews`). */
export function AppPreviewsClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  return (
    <AppPreviewsScreen {...useAppPreviews(slug)} configHref={appPath(chrome, slug, "config")} />
  );
}
