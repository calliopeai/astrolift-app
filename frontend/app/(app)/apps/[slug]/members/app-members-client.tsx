"use client";

import { AppMembersScreen } from "@/components/screens/apps/settings/AppMembersScreen";
import { useAppMembers } from "@/components/screens/apps/settings/use-app-members";

/**
 * People with access, the Access tab's first section on an app or an agent.
 * The screen owns the markup; the hook owns the data.
 */
export function AppMembersClient({ slug }: { slug: string }) {
  return <AppMembersScreen {...useAppMembers(slug)} slug={slug} />;
}
