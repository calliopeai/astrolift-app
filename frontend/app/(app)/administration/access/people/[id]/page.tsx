import { LIST_MEMBERS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { PrincipalAccessClient } from "./principal-clients";

export const metadata = { title: "Access · People · Astrolift" };

/** A person's or an IdP group's Access tab (access UX design 3.2). */
export default async function PrincipalAccessPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_MEMBERS}>
      <PrincipalAccessClient param={id} />
    </PreloadQuery>
  );
}
