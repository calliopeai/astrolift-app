import type * as React from "react";

import { AppFrameContainer } from "./components/app-frame";

/**
 * Every app page sits in one frame (spec 44 §5.2): `Apps ▾ › <app>`, the
 * title row, and the one row of tabs. The frame stays mounted as the tabs
 * change, so only the body re-renders.
 */
export default async function AppDetailLayout({
  params,
  children,
}: {
  params: Promise<{ slug: string }>;
  children: React.ReactNode;
}) {
  const { slug } = await params;
  return <AppFrameContainer slug={slug}>{children}</AppFrameContainer>;
}
