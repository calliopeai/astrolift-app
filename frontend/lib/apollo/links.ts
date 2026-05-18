import { ApolloLink, HttpLink, Observable, split } from "@apollo/client";
import { setContext } from "@apollo/client/link/context";
import { onError } from "@apollo/client/link/error";
import { CombinedGraphQLErrors } from "@apollo/client/errors";
import { getMainDefinition } from "@apollo/client/utilities";
import { GraphQLWsLink } from "@apollo/client/link/subscriptions";
import { createClient } from "graphql-ws";
import { getClientToken, clearToken } from "@/lib/auth/token-store";
import { getActiveOrgGuid } from "@/lib/identity/active-org";
import {
  STEP_UP_EVENT,
  type StepUpEventDetail,
} from "@/lib/auth/step-up-events";

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

// Apollo Client v4 changed onError's signature: a single `error` is
// passed (use CombinedGraphQLErrors.is(error) to detect GraphQL ones)
// and any other error type is treated as a network/protocol error.
//
// On UNAUTHENTICATED we dispatch ``astrolift:session-expired`` rather
// than forcing a redirect — ``SessionExpiredModal`` (mounted in the
// app-shell layout) picks the event up and shows an in-page modal so
// the operator can copy any unsaved draft before re-authing. The
// modal then routes them to /auth/login itself. The token is cleared
// here so any subsequent request also fails fast and re-dispatches.
const errorLink = onError(({ error }) => {
  if (CombinedGraphQLErrors.is(error)) {
    for (const { extensions } of error.errors) {
      if (extensions?.code === "UNAUTHENTICATED") {
        clearToken();
        if (typeof window !== "undefined") {
          window.dispatchEvent(new CustomEvent("astrolift:session-expired"));
        }
        return;
      }
    }
    return;
  }
  console.error("[Apollo] Network error:", error);
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

// #487 — server-side step-up afterware. The backend gates sensitive
// mutations on a session-scoped elevation timer; the deny path
// returns the MutationResult envelope with ``errors[].code ===
// "STEP_UP_REQUIRED"`` so the operator can re-auth without losing
// the form they had open. This link inspects mutation responses,
// dispatches ``astrolift:step-up-required`` with enough context for
// the modal to render the right copy + offer a retry, and forwards
// the original response so the caller's loading state resolves
// (otherwise we'd leave every mutation hanging behind the modal).
const stepUpLink = new ApolloLink((operation, forward) => {
  return new Observable((observer) => {
    const sub = forward(operation).subscribe({
      next: (result) => {
        try {
          const op = operation.query.definitions.find(
            (d) =>
              d.kind === "OperationDefinition" && d.operation === "mutation",
          );
          if (op && result.data && typeof result.data === "object") {
            for (const value of Object.values(result.data)) {
              if (
                value &&
                typeof value === "object" &&
                "errors" in value &&
                Array.isArray((value as { errors: unknown[] }).errors)
              ) {
                const errs = (
                  value as {
                    errors: {
                      code?: string;
                      message?: string;
                      supportedMethods?: string[] | null;
                    }[];
                  }
                ).errors;
                const stepUp = errs.find((e) => e.code === "STEP_UP_REQUIRED");
                if (stepUp && typeof window !== "undefined") {
                  // #526 — propagate supportedMethods from the deny
                  // envelope so the modal can branch SSO → IdP redirect
                  // vs. password → form. Backend may return null when
                  // the session bag hasn't been stamped yet; the modal
                  // treats undefined as the legacy password-only case.
                  const raw = stepUp.supportedMethods;
                  const known = ["password", "sso", "webauthn", "magic_link"];
                  const supported = Array.isArray(raw)
                    ? (raw.filter((m): m is string => typeof m === "string" && known.includes(m)) as (
                        | "password"
                        | "sso"
                        | "webauthn"
                        | "magic_link"
                      )[])
                    : undefined;
                  const detail: StepUpEventDetail = {
                    operationName: operation.operationName ?? "",
                    message: stepUp.message ?? "",
                    supportedMethods: supported,
                  };
                  window.dispatchEvent(
                    new CustomEvent(STEP_UP_EVENT, { detail }),
                  );
                }
              }
            }
          }
        } catch (e) {
          // Inspection must never blow up the response — log + carry on.
          console.error("[Apollo] step-up afterware:", e);
        }
        observer.next(result);
      },
      error: (err) => observer.error(err),
      complete: () => observer.complete(),
    });
    return () => sub.unsubscribe();
  });
});

export function buildClientLinks(httpLink: HttpLink): ApolloLink {
  const wsLink = buildWsLink();
  const httpStack = ApolloLink.from([
    errorLink,
    stepUpLink,
    authLink.concat(httpLink),
  ]);
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
