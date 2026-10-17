import type * as React from "react";

import { BrainFrameContainer } from "./components/brain-frame";

/**
 * Every semantic layer page sits in one frame (spec 44 §5.2, #2427):
 * `Apps ▾ › Semantic layers › <layer>`, the title row with Connect, and the
 * one row of tabs. The frame stays mounted as the tabs change, so only the
 * body re-renders.
 */
export default async function BrainDetailLayout({
  params,
  children,
}: {
  params: Promise<{ slug: string }>;
  children: React.ReactNode;
}) {
  const { slug } = await params;
  return <BrainFrameContainer slug={slug}>{children}</BrainFrameContainer>;
}
