import { routeTitle } from "@/lib/i18n/route-title";

import { InvitationAcceptClient } from "./invitation-accept-client";

export const generateMetadata = routeTitle("acceptInvitation");

export default async function InvitationAcceptPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return <InvitationAcceptClient token={token} />;
}
