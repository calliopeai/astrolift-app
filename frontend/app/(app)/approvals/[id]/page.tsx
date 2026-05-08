import { PreloadQuery } from "@/lib/apollo";
import { GET_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.queries";

import { ApprovalClient } from "./approval-client";

export const metadata = { title: "Approve deployment · Astrolift" };

export default async function ApprovalPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={GET_DEPLOYMENT} variables={{ id }}>
      <ApprovalClient id={id} />
    </PreloadQuery>
  );
}
