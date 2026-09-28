import { AdministrationShell } from "@/components/screens/administration/organization/AdministrationShell";
import { LIST_API_TOKENS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AdministrationSubnav } from "../administration/administration-subnav";
import { TokensClient } from "./tokens-client";

export const metadata = { title: "API keys · Astrolift" };

// /tokens is the canonical Tokens surface but lives at the top level rather
// than under /administration (see administration-subnav.tsx, #893). Render the
// same Administration sub-navigation here so the page keeps its place in the
// admin control plane — Tokens highlighted, every sibling section one click
// away — instead of stranding the operator on a page with no way back.
export default function TokensPage() {
  return (
    <AdministrationShell subnav={<AdministrationSubnav />}>
      <PreloadQuery query={LIST_API_TOKENS}>
        <TokensClient />
      </PreloadQuery>
    </AdministrationShell>
  );
}
