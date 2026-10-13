import { PreloadQuery } from "@/lib/apollo";
import { GET_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.queries";
import { routeTitle } from "@/lib/i18n/route-title";

import { ApprovalClient } from "./approval-client";

export const generateMetadata = routeTitle("approveDeployment");

export default async function ApprovalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PreloadQuery query={GET_DEPLOYMENT} variables={{ id }}>
      <ApprovalClient id={id} />
    </PreloadQuery>
  );
}
