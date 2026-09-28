"use client";

import { AppMembersScreen } from "@/components/screens/apps/settings/AppMembersScreen";
import { useAppMembers } from "@/components/screens/apps/settings/use-app-members";

import { AppTabs } from "../components/app-tabs";

/** App members tab. The screen owns the markup; the hook owns the data. */
export function AppMembersClient({ slug }: { slug: string }) {
  const members = useAppMembers(slug);
  return (
    <AppMembersScreen
      {...members}
      slug={slug}
      tabs={members.app ? <AppTabs slug={members.app.slug} active="members" /> : undefined}
    />
  );
}
