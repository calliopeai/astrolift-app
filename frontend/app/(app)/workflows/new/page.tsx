"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { ConfigureWorkflowScreen } from "@/components/screens/workflows/new/ConfigureWorkflowScreen";
import { useConfigureWorkflow } from "@/components/screens/workflows/new/use-configure-workflow";

// ─── Configure-to-run form (?definition=<slug>) ──────────────────────────────

function ConfigureFromDefinition({ definitionSlug }: { definitionSlug: string }) {
  return <ConfigureWorkflowScreen {...useConfigureWorkflow(definitionSlug)} />;
}

// ─── Page ─────────────────────────────────────────────────────────────────────

function NewWorkflowPageInner() {
  const router = useRouter();
  const definitionSlug = useSearchParams().get("definition");

  // Without ?definition= there is nothing to configure here — the
  // pattern-picker / manifest-import flow at /workflows/builder is the
  // way to author a new definition.
  useEffect(() => {
    if (!definitionSlug) {
      router.replace("/workflows/builder");
    }
  }, [definitionSlug, router]);

  if (!definitionSlug) return null;
  return <ConfigureFromDefinition definitionSlug={definitionSlug} />;
}

export default function NewWorkflowPage() {
  return (
    <Suspense fallback={null}>
      <NewWorkflowPageInner />
    </Suspense>
  );
}
