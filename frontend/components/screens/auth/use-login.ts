"use client";

import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { authCallbackUrl, deviceApprovalReturnPath } from "@/lib/auth/device-return";

// In dev (NEXT_PUBLIC_DEV_LOGIN=1) we honour the legacy dev-login
// bypass and skip everything else. In any other build we read the
// active IdP from /app/auth1/active-idp.json and render the right
// CTA — Auth0 / Cognito / OIDC button, or a username+password form
// when kind=local.
const useDevLogin = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";

export interface ActiveIdp {
  kind: string | null;
  displayName: string;
  loginPath: string | null;
  ready: boolean;
  hint?: string;
}

/** Everything the login screen does against the API: IdP detection and the two login paths. */
export function useLogin() {
  const router = useRouter();
  const [idp, setIdp] = React.useState<ActiveIdp | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const apiRoot = process.env.NEXT_PUBLIC_API_ROOT ?? "";
  const returnPath = () =>
    deviceApprovalReturnPath(new URLSearchParams(window.location.search).get("next"));

  React.useEffect(() => {
    if (useDevLogin) {
      // Wipe any stale JWT from a prior session — dev-login uses a
      // sessionid cookie, and a leftover Bearer JWT will trip the
      // backend's Auth0SessionMiddleware and 401 every GraphQL call.
      try {
        window.localStorage.removeItem("jwt");
      } catch {
        // localStorage may be unavailable in some browser modes.
      }
      window.location.href = `${apiRoot}/app/auth1/dev-login?next=${encodeURIComponent(returnPath())}`;
      return;
    }
    fetch(`${apiRoot}/app/auth1/active-idp.json`, { credentials: "include" })
      .then((r) => r.json())
      .then(setIdp)
      .catch((err) => setLoadError(String(err)));
  }, [apiRoot]);

  /** External IdP: kick off the auth1 round-trip. */
  function continueWithIdp() {
    const loginPath = idp?.loginPath ?? "/app/auth1/login";
    const callback = authCallbackUrl(window.location.origin, returnPath());
    window.location.href = `${apiRoot}${loginPath}?next=${encodeURIComponent(callback)}`;
  }

  /** kind=local: post the credentials, then go to the return path. Failures toast. */
  async function submitLocal(username: string, password: string): Promise<void> {
    const next = returnPath();
    try {
      const res = await fetch(`${apiRoot}/app/auth1/local-login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        credentials: "include",
        body: JSON.stringify({ username, password, next }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        toast.error(body.detail ?? `Login failed (${res.status})`);
        return;
      }
      if (next === "/dashboard") router.push(next);
      else window.location.assign(next);
    } catch (err) {
      toast.error(String(err));
    }
  }

  return { devLogin: useDevLogin, idp, loadError, continueWithIdp, submitLocal };
}
