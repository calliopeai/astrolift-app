import { GET_SECRET_CHANGE_PROPOSAL } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { SecretProposalDetailClient } from "./secret-proposal-detail-client";

export const generateMetadata = routeTitle("secretProposal", "approvals");

export default async function SecretProposalDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={GET_SECRET_CHANGE_PROPOSAL} variables={{ id }}>
      <SecretProposalDetailClient proposalId={id} />
    </PreloadQuery>
  );
}
