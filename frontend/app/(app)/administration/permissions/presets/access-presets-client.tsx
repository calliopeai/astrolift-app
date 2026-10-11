"use client";

import { AccessPresetsView } from "@/components/screens/administration/permissions/AccessPresetsView";
import { useAccessPresets } from "@/components/screens/administration/permissions/use-access-presets";

/** Admin › Permissions › Easy setup (#2272), mounted only while the permissions console is on. */
export function AccessPresetsContent() {
  return <AccessPresetsView {...useAccessPresets()} />;
}
