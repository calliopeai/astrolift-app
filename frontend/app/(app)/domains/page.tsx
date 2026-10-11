import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { DomainsClient } from "./domains-client";

export const metadata = { title: "Domains · Astrolift" };

export default async function DomainsPage({
  searchParams,
}: {
  searchParams: Promise<{ connectionOutcome?: string }>;
}) {
  const { connectionOutcome } = await searchParams;
  const outcome =
    connectionOutcome === "saved" ||
    connectionOutcome === "unconfirmed" ||
    connectionOutcome === "denied"
      ? connectionOutcome
      : undefined;
  return (
    <PreloadQuery query={LIST_MANAGED_DOMAINS}>
      <DomainsClient connectionOutcome={outcome} />
    </PreloadQuery>
  );
}
