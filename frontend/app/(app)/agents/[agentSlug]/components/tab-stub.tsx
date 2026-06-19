"use client";

import { ConstructionIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Placeholder for a BROCS pillar whose content lands in a later PR (spec 33).
 * The Run tab (Dispatch-now + Executions) is PR-10; the Control tab (run-spec
 * editor) is PR-11/12. Rendered as a clearly-marked stub so the tab exists and
 * routes correctly without implying functionality that isn't built yet.
 */
export function TabStub({ title, description }: { title: string; description: string }) {
  return (
    <Card>
      <CardContent className="p-6">
        <EmptyState
          icon={<ConstructionIcon className="size-5" />}
          title={title}
          description={description}
        />
      </CardContent>
    </Card>
  );
}
