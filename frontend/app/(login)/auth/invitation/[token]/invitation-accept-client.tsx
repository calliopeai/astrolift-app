"use client";

import { InvitationAccept } from "@/components/screens/auth/InvitationAccept";
import { useInvitationAccept } from "@/components/screens/auth/use-invitation-accept";

export function InvitationAcceptClient({ token }: { token: string }) {
  return <InvitationAccept {...useInvitationAccept(token)} />;
}
