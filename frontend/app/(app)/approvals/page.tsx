import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_SECRET_CHANGE_PROPOSALS } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ApprovalsQueueClient } from "./approvals-queue-client";
import { SecretProposalsQueueClient } from "./secret-proposals-queue-client";

export const metadata = { title: "Approvals queue · Astrolift" };

// #420 / #488 — global approvals queue. Lists pending deployment
// approvals + pending secret-change proposals side by side; both axes
// pre-loaded so the page renders without a client roundtrip.
export default function ApprovalsPage() {
  return (
    <PreloadQuery query={LIST_DEPLOYMENTS} variables={{ limit: 100 }}>
      <PreloadQuery
        query={LIST_SECRET_CHANGE_PROPOSALS}
        variables={{ appSlug: null, status: "pending" }}
      >
        <ApprovalsQueueClient>
          <SecretProposalsQueueClient />
        </ApprovalsQueueClient>
      </PreloadQuery>
    </PreloadQuery>
  );
}
