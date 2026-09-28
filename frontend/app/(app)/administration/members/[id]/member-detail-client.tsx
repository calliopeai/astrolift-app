"use client";

import { MemberDetail } from "@/components/screens/administration/access/MemberDetail";
import { useMemberDetail } from "@/components/screens/administration/access/use-member-detail";

/** Member detail (#1106), wired to one member id. */
export function MemberDetailClient({ id }: { id: string }) {
  return <MemberDetail {...useMemberDetail(id)} />;
}
