import { LIST_API_TOKENS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { TokensClient } from "./tokens-client";

export const metadata = { title: "API tokens · Astrolift" };

export default function TokensPage() {
  return (
    <PreloadQuery query={LIST_API_TOKENS}>
      <TokensClient />
    </PreloadQuery>
  );
}
