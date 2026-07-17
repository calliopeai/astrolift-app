import { PreloadQuery } from "@/lib/apollo";
import { LIST_MEMBERS, LIST_ROLE_BINDINGS } from "@/graphql/identity/identity.queries";

import { MemberDetailClient } from "./member-detail-client";

export const metadata = { title: "Member · Astrolift" };

/**
 * Member detail (#1106) — drill-in target for an /administration/members row.
 * Reuses LIST_MEMBERS + LIST_ROLE_BINDINGS (no singular query exists).
 */
export default async function MemberDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_MEMBERS}>
      <PreloadQuery query={LIST_ROLE_BINDINGS}>
        <MemberDetailClient id={id} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
