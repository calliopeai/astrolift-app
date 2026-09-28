"use client";

import { AppPreviewsScreen } from "@/components/screens/apps/security/AppPreviewsScreen";
import { useAppPreviews } from "@/components/screens/apps/security/use-app-previews";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";

export function AppPreviewsClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const previews = useAppPreviews(slug);
  const a = previews.app;

  return (
    <AppPreviewsScreen
      {...previews}
      configHref={appPath(chrome, a?.slug ?? slug, "config")}
      tabs={a ? <AppTabs slug={a.slug} active="previews" /> : null}
    />
  );
}
