import { LIST_API_TOKENS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { TokensClient } from "@/app/(app)/tokens/tokens-client";

export const metadata = { title: "API tokens · Astrolift" };

export default function AdministrationTokensPage() {
  return (
    <PreloadQuery query={LIST_API_TOKENS}>
      <TokensClient />
    </PreloadQuery>
  );
}
