import { LIST_ROLES } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { MembersClient } from "@/app/(app)/members/members-client";

export const metadata = {
  title: "Members · Astrolift",
};

/**
 * Only the role catalog is preloaded. The three tables below it walk
 * cursor pages through `useCursorTable`, whose first request carries a
 * `limit` (and a search term restored from the URL), so a variable-less
 * SSR preload would never be the request the client makes — it would be
 * a second round trip against the deprecated unbounded list field, and
 * the table would still render its skeleton. `LIST_ROLES` is a plain
 * lookup the Grant-role dialog reads, so preloading it still pays.
 */
export default function AdministrationMembersPage() {
  return (
    <PreloadQuery query={LIST_ROLES}>
      <MembersClient />
    </PreloadQuery>
  );
}
