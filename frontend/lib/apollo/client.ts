import { ApolloLink, HttpLink } from "@apollo/client";
import { registerApolloClient, ApolloClient } from "@apollo/client-integration-nextjs";
import { cookies } from "next/headers";
import { fetchToken } from "@/lib/auth/fetch-token";
import { makeCache } from "./cache";

const API_URL = `${process.env.NEXT_PUBLIC_API_ROOT}/app/gql/config/`;

// Cookies the SSR client forwards to the backend. The dev-login flow
// uses Django's sessionid; production OIDC uses backend_jwt + a CSRF
// pair. Forwarding the whole jar keeps SSR-time GraphQL queries
// (PreloadQuery) authenticated under whatever auth backend is wired —
// otherwise the SSR call goes out cookieless and gets a 401, which
// primes the Apollo cache with an empty result and hides every
// permission-gated button on first paint.
const COOKIE_PASSTHROUGH = new Set([
  "sessionid",
  "csrftoken",
  "backend_jwt",
  "astrolift_active_org",
]);

export const { getClient, query, PreloadQuery } = registerApolloClient(async () => {
  const cookieStore = await cookies();
  const cachedToken = cookieStore.get("backend_jwt")?.value;
  const token = cachedToken ?? (await fetchToken());

  const forwardedCookies = cookieStore
    .getAll()
    .filter((c) => COOKIE_PASSTHROUGH.has(c.name))
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");

  const authLink = new ApolloLink((operation, forward) => {
    operation.setContext(({ headers = {} }: { headers?: Record<string, string> }) => ({
      headers: {
        ...headers,
        ...(token ? { Authorization: token } : {}),
        ...(forwardedCookies ? { Cookie: forwardedCookies } : {}),
        "x-platform": "web",
      },
    }));
    return forward(operation);
  });

  const httpLink = new HttpLink({ uri: API_URL });

  return new ApolloClient({
    cache: makeCache(),
    link: ApolloLink.from([authLink, httpLink]),
  });
});
