"use client";

/**
 * Layout-level surfaceer for the OAuth callback's ``?scm_connected`` /
 * ``?scm_error`` query params (#759). The SCM OAuth dance redirects
 * the browser back to the caller's ``return_to`` after the host
 * (GitHub / GitLab / Bitbucket) hands the code back to Django. The
 * ``return_to`` defaults to ``/`` for the Account-drawer "Connect"
 * button, so the operator commonly lands on the dashboard — where
 * the in-page handler on ``/settings/source-providers`` never fires.
 *
 * Mounting this client component in ``(app)/layout.tsx`` guarantees
 * the toast surfaces regardless of where the operator lands. The
 * effect strips the query params from the URL after firing so a
 * refresh doesn't re-toast.
 */

import * as React from "react";
import { toast } from "sonner";

const ERROR_MESSAGES: Record<string, string> = {
  app_base_url_not_public:
    "GitHub can't reach this install. Set APP_BASE_URL on the backend to your public URL and redeploy.",
  config_missing_oauth_secret:
    "This GitHub App is missing its OAuth client_secret. Re-register the App via the App manifest flow, or set the secret manually on the SourceConnection row.",
  config_missing_client_id:
    "This GitHub connection is missing its Client ID — add it via 'Add Client ID' on Settings → Source providers.",
  state_mismatch: "OAuth flow expired or replayed. Click Connect to try again.",
  no_pending_state: "OAuth flow expired. Click Connect to try again.",
  no_code: "GitHub didn't return an authorization code. Click Connect to retry.",
  config_gone: "The SCM connection was removed mid-flow. Re-create it and try again.",
  org_mismatch: "OAuth flow started in a different org. Switch org and try again.",
  exchange_failed:
    "GitHub rejected the auth code. The App's client_secret on this connection may be wrong.",
};

function describe(err: string): string {
  if (ERROR_MESSAGES[err]) return ERROR_MESSAGES[err];
  return `SCM OAuth: ${err.replace(/_/g, " ")}`;
}

export function ScmCallbackToast() {
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    const ok = url.searchParams.get("scm_connected");
    const err = url.searchParams.get("scm_error");
    if (!ok && !err) return;

    if (ok) toast.success(`Connected ${ok}`);
    else if (err) toast.error(describe(err), { duration: 10_000 });

    url.searchParams.delete("scm_connected");
    url.searchParams.delete("scm_error");
    window.history.replaceState({}, "", url.toString());
  }, []);

  return null;
}
