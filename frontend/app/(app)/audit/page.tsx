import { LIST_AUDIT_EVENTS } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AuditClient } from "./audit-client";

export const metadata = { title: "Audit · Astrolift" };

export default function AuditPage() {
  return (
    <PreloadQuery
      query={LIST_AUDIT_EVENTS}
      variables={{ limit: 200, action: null, decision: null }}
    >
      <AuditClient />
    </PreloadQuery>
  );
}
