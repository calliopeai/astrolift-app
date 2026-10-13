import { LIST_SECRET_CHANGE_PROPOSALS_PAGE } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";
import { SecretProposalsListClient } from "./secret-proposals-list-client";
export const generateMetadata = routeTitle("secretProposals", "approvals");
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
