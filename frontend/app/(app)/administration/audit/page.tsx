import {
  GET_AUDIT_RETENTION,
  LIST_AUDIT_EVENTS_PAGE,
} from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AuditClient } from "./audit-client";

export const metadata = { title: "Audit · Astrolift" };

export default function AuditPage() {
  return (
    <>
      {/* These variables have to be exactly the ones `useCursorTable`
          asks for on its first page — Apollo keys the cache entry on
          the variable set, so a stale `after`/`actorId` here would warm
          an entry the client never reads and the table would still
          paint its loading skeleton. */}
      <PreloadQuery
        query={LIST_AUDIT_EVENTS_PAGE}
        variables={{
          limit: 100,
          action: null,
          decision: null,
          createdAtGte: null,
          createdAtLte: null,
          includeTotal: true,
        }}
      >
        <PreloadQuery query={GET_AUDIT_RETENTION} variables={{}}>
          <AuditClient />
        </PreloadQuery>
      </PreloadQuery>
    </>
  );
}
