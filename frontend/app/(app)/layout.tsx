import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { CombinedGraphQLErrors } from "@apollo/client/errors";

import { AppShellContainer } from "./_shell/app-shell";
import { LiveRegionProvider } from "@/providers/LiveRegion";
import { ScmCallbackToast } from "@/providers/ScmCallbackToast";
import { AppearancePolicyBridge } from "@/providers/AppearancePolicyBridge";
import { UiPreferencesBridge } from "@/providers/UiPreferencesBridge";
import { SessionExpiredModal } from "@/components/SessionExpiredModal";
import { SkipToContent } from "@/components/SkipToContent";
import { getClient } from "@/lib/apollo";
import { ActiveOrgProvider } from "@/graphql/identity/identity.hooks";
import { GET_ME } from "@/graphql/user/user.queries";
import { GET_MY_PERMISSIONS } from "@/graphql/permissions/astrolift.queries";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import type { MeQueryData, MeQueryVariables } from "@/graphql/user/user.types";

// A caught SSR identity error is an auth failure (send to /auth/login) only
// when it's a GraphQL UNAUTHENTICATED or an HTTP 401 — mirrors the client
// errorLink. Anything else (network blip, 5xx) is transient and must NOT log
// an authenticated operator out.
function isUnauthenticated(err: unknown): boolean {
  if (CombinedGraphQLErrors.is(err)) {
    return err.errors.some((e) => e.extensions?.code === "UNAUTHENTICATED");
  }
  const status =
    (err as { statusCode?: number })?.statusCode ??
    (err as { networkError?: { statusCode?: number } })?.networkError?.statusCode;
  return status === 401;
}

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  // Check for auth token in cookies — if no token at all, redirect to login
  const cookieStore = await cookies();
  const hasToken = cookieStore.has("backend_jwt") || cookieStore.has("sessionid");

  if (!hasToken) {
    redirect("/auth/login");
  }

  let sessionInvalid = false;

  try {
    const client = await getClient();
    // Fetch all three identity queries in parallel (#822).
    // Previously nested PreloadQuery components created a sequential
    // waterfall (GET_ME → GET_MY_PERMISSIONS → LIST_ORGANIZATIONS) that
    // caused a 1-2s blank screen before the sidebar and page content
    // rendered. Promise.all reduces this to one round-trip time. getClient()
    // serialises all results into the Apollo SSR cache automatically, so
    // client-side useQuery hooks find data immediately (no loading flash).
    await Promise.all([
      client.query<MeQueryData, MeQueryVariables>({ query: GET_ME }),
      client.query({ query: GET_MY_PERMISSIONS }),
      client.query({ query: LIST_ORGANIZATIONS }),
    ]);
  } catch (err) {
    // A stale/expired token cookie is still *present* (so the cookie check
    // above passes), but the identity query comes back UNAUTHENTICATED. Treat
    // that exactly like no token at all — redirect to /auth/login rather than
    // rendering a broken dashboard. redirect() must run OUTSIDE this try (it
    // throws a control-flow signal that the catch would otherwise swallow), so
    // flag it and redirect below. Transient NETWORK errors are NOT auth
    // failures — leave those to the client errorLink so a blip doesn't bounce
    // an authenticated operator.
    if (isUnauthenticated(err)) {
      sessionInvalid = true;
    }
  }

  if (sessionInvalid) {
    redirect("/auth/login");
  }

  return (
    <LiveRegionProvider>
      {/* Resolves the install's org once for the whole (app) tree and pins
          the org cookie before any child renders — every useActiveOrg
          consumer gates its org-scoped queries on it (#1022). */}
      <ActiveOrgProvider>
        <SkipToContent />
        {/* The org's house theme and the person's server UI preferences
            (#135, #2154) reach the browser-stored preferences here. */}
        <AppearancePolicyBridge />
        <UiPreferencesBridge />
        <AppShellContainer>{children}</AppShellContainer>
        <ScmCallbackToast />
        <SessionExpiredModal />
      </ActiveOrgProvider>
    </LiveRegionProvider>
  );
}
