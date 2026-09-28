import { LIST_API_TOKENS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { TokensClient } from "./tokens-client";

export const metadata = { title: "API keys · Astrolift" };

// /tokens is the canonical Tokens surface but lives at the top level rather
// than under /administration (#893). It keeps its place in the admin control
// plane through the rail's Admin › Usage & governance › API keys row and the
// `Admin ▾` switcher on every Admin page.
export default function TokensPage() {
  return (
    <PreloadQuery query={LIST_API_TOKENS}>
      <TokensClient />
    </PreloadQuery>
  );
}
