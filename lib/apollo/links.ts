import { ApolloLink, HttpLink } from "@apollo/client";
import { setContext } from "@apollo/client/link/context";
import { onError } from "@apollo/client/link/error";
import { getClientToken, clearToken } from "@/lib/auth/token-store";
import { getActiveOrgGuid } from "@/lib/identity/active-org";

const authLink = setContext(async (_, { headers }) => {
  const token = await getClientToken();
  // The OrgSwitcher writes the active org's guid to a cookie; we
  // mirror it on every GraphQL request as X-Astrolift-Organization
  // so the backend's TenantContextMiddleware scopes the resolver.
  const orgGuid = getActiveOrgGuid();
  return {
    headers: {
      ...headers,
      ...(token ? { Authorization: token } : {}),
      ...(orgGuid ? { "X-Astrolift-Organization": orgGuid } : {}),
      "x-platform": "web",
    },
  };
});

const errorLink = onError(({ graphQLErrors, networkError }) => {
  if (graphQLErrors) {
    for (const { extensions } of graphQLErrors) {
      if (extensions?.code === "UNAUTHENTICATED") {
        clearToken();
        window.location.href = "/auth/login";
        return;
      }
    }
  }
  if (networkError) {
    console.error("[Apollo] Network error:", networkError);
  }
});

export function buildClientLinks(httpLink: HttpLink): ApolloLink {
  return ApolloLink.from([errorLink, authLink.concat(httpLink)]);
}
