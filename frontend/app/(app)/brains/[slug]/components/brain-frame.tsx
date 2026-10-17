"use client";

import type * as React from "react";

import { BrainFrame } from "@/components/screens/brains/detail/BrainFrame";
import { useBrainFrame } from "@/components/screens/brains/detail/use-brain-frame";
import { AppPermissionsProvider } from "@/lib/permissions/app-permissions";

import { FramedBrainContext } from "./framed-brain";

/**
 * The frame around every `/brains/[slug]/*` route: the header and the one
 * row of tabs (BrainFrame), with the route's own client below it. A layer
 * is an app, so the app's own permissions answer what the viewer may do
 * here, in the header and in every tab.
 */
export function BrainFrameContainer({
  slug,
  children,
}: {
  slug: string;
  children: React.ReactNode;
}) {
  return (
    <AppPermissionsProvider slug={slug}>
      <FramedRoute slug={slug}>{children}</FramedRoute>
    </AppPermissionsProvider>
  );
}

function FramedRoute({ slug, children }: { slug: string; children: React.ReactNode }) {
  const { frame, framed } = useBrainFrame(slug);
  return (
    <BrainFrame {...frame}>
      <FramedBrainContext.Provider value={framed}>{children}</FramedBrainContext.Provider>
    </BrainFrame>
  );
}
