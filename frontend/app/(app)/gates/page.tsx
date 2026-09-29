import { LIST_PENDING_HUMAN_GATES } from "@/graphql/workflows/tiered.queries";
import { PreloadQuery } from "@/lib/apollo";

import { PendingGatesClient } from "./pending-gates-client";

export const metadata = { title: "Pending Gates · Astrolift" };

// #1820, org-wide pending-gates list: every open human_gate across the
// org's workflow runs that the caller may decide, in one place, so an
// approver does not have to open every workflow to find them. Reviewing and
// deciding stays on the run's observe page (#2068's GateReview); each row
// here links straight to it.
export default function PendingGatesPage() {
  return (
    <PreloadQuery query={LIST_PENDING_HUMAN_GATES} variables={{ orgId: null, limit: 50 }}>
      <PendingGatesClient />
    </PreloadQuery>
  );
}
