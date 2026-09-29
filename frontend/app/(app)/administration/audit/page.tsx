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
      {/* These variables have to be exactly the ones `useAuditLog` sends
          for the default list state (All, no chips, the feed's newest
          page; see `auditVariables` and useCursorFeed, which leaves the
          cursor off the newest page). Apollo keys the cache entry on the variable
          set, so any difference warms an entry the client never reads
          and the list would still paint its loading skeleton. */}
      <PreloadQuery
        query={LIST_AUDIT_EVENTS_PAGE}
        variables={{ limit: 100, search: null, filter: null, includeTotal: true }}
      >
        <PreloadQuery query={GET_AUDIT_RETENTION} variables={{}}>
          <AuditClient />
        </PreloadQuery>
      </PreloadQuery>
    </>
  );
}
