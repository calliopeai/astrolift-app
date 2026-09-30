"use client";

import { usePathname } from "next/navigation";
import type * as React from "react";

import { AppChromeProvider } from "@/lib/app-chrome-context";
import { useWorkflowFrame } from "@/components/screens/workflows/detail/use-workflow-frame";
import { WorkflowFrame } from "@/components/screens/workflows/detail/WorkflowFrame";

import { FramedWorkflowContext } from "./framed-workflow";

/**
 * The frame around every `/workflows/[slug]/*` route: the header and the one
 * row of tabs (WorkflowFrame), with the route's own client below it. The
 * `framed` chrome drops the title of any `PageShell` a tab still carries
 * (the stage builder's), so the frame's header is the only one.
 */
export function WorkflowFrameContainer({
  slug,
  children,
}: {
  slug: string;
  children: React.ReactNode;
}) {
  const { frame, framed } = useWorkflowFrame(slug);
  const body = (
    <FramedWorkflowContext.Provider value={framed}>
      <AppChromeProvider framed>{children}</AppChromeProvider>
    </FramedWorkflowContext.Provider>
  );
  // A run's own page is the run archetype (spec 44 §5.5): its own header and
  // no tabs, so it gets the workflow but not the frame around it.
  const pathname = usePathname() ?? "";
  // Cold run links still need the frame's loading/error boundary until its
  // workflow exists; the run body reads that context during its first render.
  if (/\/runs\/[^/]+$/.test(pathname) && framed) return body;
  return <WorkflowFrame {...frame}>{body}</WorkflowFrame>;
}
