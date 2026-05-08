import { ApolloLink, HttpLink, split } from "@apollo/client";
import { setContext } from "@apollo/client/link/context";
import { onError } from "@apollo/client/link/error";
import { getMainDefinition } from "@apollo/client/utilities";
import { GraphQLWsLink } from "@apollo/client/link/subscriptions";
import { createClient } from "graphql-ws";
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

function buildWsLink(): GraphQLWsLink | null {
  if (typeof window === "undefined") return null;
  // Next.js rewrites only proxy HTTP, not WebSocket upgrades, so
  // the browser must talk to the backend directly. NEXT_PUBLIC_WS_ORIGIN
  // is the browser-resolvable backend (e.g. ws://localhost:8000 in
  // docker dev). When unset, we ride same-origin — correct in prod
  // where a single LB terminates both HTTP and WS.
  const wsOrigin = process.env.NEXT_PUBLIC_WS_ORIGIN;
  let url: string;
  if (wsOrigin) {
    url = `${wsOrigin.replace(/\/$/, "")}/app/gql/config/ws/`;
  } else {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    url = `${proto}//${window.location.host}/app/gql/config/ws/`;
  }
  return new GraphQLWsLink(
    createClient({
      url,
      // Reconnect with backoff if the server drops or the network
      // hiccups. The session cookie persists across reconnects.
      retryAttempts: Infinity,
      shouldRetry: () => true,
      lazy: true,
    }),
  );
}

export function buildClientLinks(httpLink: HttpLink): ApolloLink {
  const wsLink = buildWsLink();
  const httpStack = ApolloLink.from([errorLink, authLink.concat(httpLink)]);
  if (!wsLink) return httpStack;
  // Subscription operations get the WS link; everything else
  // (queries, mutations) goes through the HTTP stack so the
  // existing auth + error handling is untouched.
  return split(
    ({ query }) => {
      const def = getMainDefinition(query);
      return (
        def.kind === "OperationDefinition" &&
        def.operation === "subscription"
      );
    },
    wsLink,
    httpStack,
  );
}
