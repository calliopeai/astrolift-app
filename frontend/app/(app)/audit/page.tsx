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
      <PreloadQuery
        query={LIST_AUDIT_EVENTS_PAGE}
        variables={{
          limit: 100,
          after: null,
          action: null,
          decision: null,
          actorId: null,
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
