import { PreloadQuery } from "@/lib/apollo";
import {
  GET_DEPLOYMENT,
  GET_DEPLOYMENT_LOG,
} from "@/graphql/lifecycle/lifecycle.queries";

import { DeploymentDetailClient } from "./deployment-detail-client";

export const metadata = { title: "Deployment · Astrolift" };

export default async function DeploymentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={GET_DEPLOYMENT} variables={{ id }}>
      <PreloadQuery query={GET_DEPLOYMENT_LOG} variables={{ deploymentId: id }}>
        <DeploymentDetailClient id={id} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
