"use client";

import { useQuery } from "@apollo/client/react";

import type { AstroliftApiTokenScopeCatalog } from "@/graphql/__generated__/schema";
import { GET_API_TOKEN_SCOPE_CATALOG } from "@/graphql/identity/identity.queries";

/** The server's token scope catalog (#2120). The data half of ScopePicker. */
export function useScopeCatalog() {
  const { data, loading, error } = useQuery<{
    astroliftApiTokenScopeCatalog: AstroliftApiTokenScopeCatalog;
  }>(GET_API_TOKEN_SCOPE_CATALOG);
  return { catalog: data?.astroliftApiTokenScopeCatalog, loading, error };
}
