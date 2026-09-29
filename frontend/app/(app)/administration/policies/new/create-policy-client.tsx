"use client";

import { PolicyEditorScreen } from "@/components/screens/administration/access/PolicyEditorScreen";
import { usePolicyEditor } from "@/components/screens/administration/access/use-policy-editor";

/** Administration › Policies › New policy: the stepped sentence editor. */
export function CreatePolicyClient() {
  return <PolicyEditorScreen {...usePolicyEditor()} />;
}
