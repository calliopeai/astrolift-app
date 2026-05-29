import { LIST_QUOTAS } from "@/graphql/billing/billing.queries";
import { PreloadQuery } from "@/lib/apollo";

import { QuotasClient } from "@/app/(app)/quotas/quotas-client";

export const metadata = { title: "Quotas · Astrolift" };

export default function AdministrationQuotasPage() {
  return (
    <PreloadQuery query={LIST_QUOTAS}>
      <QuotasClient />
    </PreloadQuery>
  );
}
