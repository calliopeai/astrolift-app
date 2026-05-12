"use client";

import { HttpLink } from "@apollo/client";
import { ApolloNextAppProvider, ApolloClient } from "@apollo/client-integration-nextjs";
import { buildClientLinks } from "./links";
import { getClientCache } from "./cache";

// In the browser the API is always same-origin (Next.js + nginx + Django all
// share the apex domain) so a relative URL works without any build-time env
// substitution. NEXT_PUBLIC_API_ROOT is only honored when explicitly set —
// which keeps the dev-time http://localhost:8000 split for split-origin
// developer setups. This avoids the production gotcha where
// `${process.env.NEXT_PUBLIC_API_ROOT}/app/gql/config/` becomes literally
// "undefined/app/gql/config/" in the client bundle because the upstream
// image was built without that env baked in.
const API_URL = process.env.NEXT_PUBLIC_API_ROOT
  ? `${process.env.NEXT_PUBLIC_API_ROOT}/app/gql/config/`
  : "/app/gql/config/";

function makeClient(): ApolloClient {
  const httpLink = new HttpLink({ uri: API_URL });
  return new ApolloClient({
    link: buildClientLinks(httpLink),
    cache: getClientCache(),
  });
}

export function ApolloWrapper({ children }: { children: React.ReactNode }) {
  return (
    <ApolloNextAppProvider makeClient={makeClient}>
      {children}
    </ApolloNextAppProvider>
  );
}
