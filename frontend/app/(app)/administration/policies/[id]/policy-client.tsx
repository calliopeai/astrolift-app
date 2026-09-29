"use client";

import { PolicyEditorScreen } from "@/components/screens/administration/access/PolicyEditorScreen";
import { usePolicyEditor } from "@/components/screens/administration/access/use-policy-editor";

/** Administration › Policies › <policy>: read as a sentence, edited the same way. */
export function PolicyClient({ id }: { id: string }) {
  return <PolicyEditorScreen {...usePolicyEditor(id)} />;
}
