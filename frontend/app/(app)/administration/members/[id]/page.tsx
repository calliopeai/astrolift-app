import { redirect } from "next/navigation";

import { PEOPLE_HREF } from "@/components/screens/administration/access/access-nav";

export const metadata = { title: "Member · Astrolift" };

/** A member's page moved to Admin › Access › People (access UX design 5). */
export default async function MemberDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`${PEOPLE_HREF}/${id}`);
}
