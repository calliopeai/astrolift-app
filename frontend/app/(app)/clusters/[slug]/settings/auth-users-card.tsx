"use client";

import { AuthUsersView } from "@/components/screens/clusters/settings/AuthUsers";
import { useAuthUsers } from "@/components/screens/clusters/settings/use-auth-users";

/** The sign-in users card (#2131) wired to one cluster. */
export function AuthUsersCard({ clusterId }: { clusterId: string }) {
  return <AuthUsersView {...useAuthUsers(clusterId)} />;
}
