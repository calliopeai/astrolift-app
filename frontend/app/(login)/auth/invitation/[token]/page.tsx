import { InvitationAcceptClient } from "./invitation-accept-client";

export const metadata = { title: "Accept invitation · Astrolift" };

export default async function InvitationAcceptPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return <InvitationAcceptClient token={token} />;
}
