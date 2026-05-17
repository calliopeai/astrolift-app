import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ApprovalsQueueClient } from "./approvals-queue-client";

export const metadata = { title: "Approvals queue · Astrolift" };

// #420 — global approvals queue. Lists every pending_approval deploy
// across the org; supports bulk approve/reject within a single env
// (per-env approver policies make cross-env bulk a non-starter).
export default function ApprovalsPage() {
  return (
    <PreloadQuery query={LIST_DEPLOYMENTS} variables={{ limit: 100 }}>
      <ApprovalsQueueClient />
    </PreloadQuery>
  );
}
