"use client";

import { ActionSafetyView } from "@/components/screens/administration/permissions/ActionSafetyView";
import { useActionSafety } from "@/components/screens/administration/permissions/use-action-safety";

/** Admin › Permissions › Action safety (#2203), mounted only while the permissions console is on. */
export function ActionSafetyContent() {
  return <ActionSafetyView {...useActionSafety()} />;
}
