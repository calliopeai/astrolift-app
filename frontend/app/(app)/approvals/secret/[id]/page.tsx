import { GET_SECRET_CHANGE_PROPOSAL } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";

import { SecretProposalDetailClient } from "./secret-proposal-detail-client";

export const metadata = { title: "Secret proposal · Approvals · Astrolift" };

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
