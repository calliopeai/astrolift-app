import { ApolloLink, HttpLink } from "@apollo/client";
import { registerApolloClient, ApolloClient } from "@apollo/client-integration-nextjs";
import { cookies } from "next/headers";
import { fetchToken } from "@/lib/auth/fetch-token";
import { makeCache } from "./cache";

// Server-side: NEXT_PUBLIC_API_ROOT is honored when set (split-origin dev
// setups, e.g. http://localhost:8000). When unset — the case for the
// upstream-published image, which is built without that env baked in —
// fall back to the in-cluster apex via API_INTERNAL_ROOT, or as a last
// resort PLATFORM_DOMAIN. This keeps SSR PreloadQuery calls from going
// out as the literal string "undefined/app/gql/config/" and 404-ing,
// which streams a failed result to the client and surfaces as a
// "Couldn't load some data" banner on first paint.
function resolveApiRoot(): string {
  const explicit = process.env.NEXT_PUBLIC_API_ROOT;
  if (explicit) return explicit.replace(/\/$/, "");
  const internal = process.env.API_INTERNAL_ROOT;
  if (internal) return internal.replace(/\/$/, "");
  const platform = process.env.PLATFORM_DOMAIN;
  if (platform) {
    const host = platform.replace(/^https?:\/\//, "").replace(/\/$/, "");
    return `https://${host}`;
  }
  return "http://localhost:8000";
}

const API_URL = `${resolveApiRoot()}/app/gql/config/`;

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
