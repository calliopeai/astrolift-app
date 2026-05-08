import { LIST_QUOTAS } from "@/graphql/billing/billing.queries";
import { PreloadQuery } from "@/lib/apollo";

import { QuotasClient } from "./quotas-client";

export const metadata = { title: "Quotas · Astrolift" };

export default function QuotasPage() {
  return (
    <PreloadQuery query={LIST_QUOTAS}>
      <QuotasClient />
    </PreloadQuery>
  );
}
