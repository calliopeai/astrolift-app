"use client";

import { InviteDialog } from "@/components/screens/members/InviteDialog";
import { MembersScreen } from "@/components/screens/members/MembersScreen";
import { useMembers } from "@/components/screens/members/use-members";

export function PeopleClient() {
  return (
    <MembersScreen {...useMembers()} renderInviteDialog={(props) => <InviteDialog {...props} />} />
  );
}
