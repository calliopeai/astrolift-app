import { LIST_SECRET_CHANGE_PROPOSALS_PAGE } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";
import { SecretProposalsListClient } from "./secret-proposals-list-client";
export const metadata = { title: "Secret proposals · Approvals · Astrolift" };
export default function SecretProposalsPage() {
  return (
    <PreloadQuery
      query={LIST_SECRET_CHANGE_PROPOSALS_PAGE}
      variables={{ appSlug: null, status: "pending", limit: 25, after: null }}
    >
      <SecretProposalsListClient />
    </PreloadQuery>
  );
}
