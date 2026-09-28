"use client";

import { GrantRoleDialog } from "@/components/screens/members/GrantRoleDialog";
import { InviteDialog } from "@/components/screens/members/InviteDialog";
import { MembersScreen } from "@/components/screens/members/MembersScreen";
import { useMembers } from "@/components/screens/members/use-members";

export function MembersClient() {
  const members = useMembers();
  return (
    <MembersScreen
      {...members}
      renderGrantDialog={(props) => <GrantRoleDialog {...props} />}
      renderInviteDialog={(props) => <InviteDialog {...props} />}
    />
  );
}
