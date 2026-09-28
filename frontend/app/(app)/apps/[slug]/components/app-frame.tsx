"use client";

import type * as React from "react";

import { AppFrame } from "@/components/screens/apps/detail/AppFrame";
import { useAppFrame } from "@/components/screens/apps/detail/use-app-frame";

import { AppChromeProvider } from "./app-chrome-context";

/**
 * The frame around every `/apps/[slug]/*` route: the header and the one row
 * of tabs (AppFrame), with the route's own client below it in `framed`
 * chrome, so its PageShell drops its title and its AppTabs render nothing.
 */
export function AppFrameContainer({ slug, children }: { slug: string; children: React.ReactNode }) {
  return (
    <AppFrame {...useAppFrame(slug)}>
      <AppChromeProvider framed>{children}</AppChromeProvider>
    </AppFrame>
  );
}
