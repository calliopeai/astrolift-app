"use client";

import { LogInIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";

/**
 * Listens for the custom ``astrolift:session-expired`` event dispatched
 * by the Apollo error link when the backend returns ``UNAUTHENTICATED``
 * for any GraphQL operation. Renders an in-page modal instead of
 * hard-redirecting the browser so the operator's tab state (other
 * cards, scroll position, open dialogs) doesn't get blown away when
 * a single resolver expires.
 *
 * Fires once per page load — the listener is unhooked after the first
 * event so repeated 401s while the operator is reading the modal don't
 * trigger the modal to re-open or stack.
 */
export function SessionExpiredModal() {
  const [open, setOpen] = React.useState(false);
  const firedRef = React.useRef(false);

  React.useEffect(() => {
    function onSessionExpired() {
      if (firedRef.current) return;
      firedRef.current = true;
      setOpen(true);
    }
    window.addEventListener("astrolift:session-expired", onSessionExpired);
    return () =>
      window.removeEventListener("astrolift:session-expired", onSessionExpired);
  }, []);

  if (!open) return null;

  // Preserve the operator's current location so a successful re-auth
  // bounces them back to the same screen instead of /dashboard.
  const returnTo =
    typeof window !== "undefined"
      ? `${window.location.pathname}${window.location.search}`
      : "/dashboard";
  const loginHref = `/auth/login?next=${encodeURIComponent(returnTo)}`;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-6"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="session-expired-title"
    >
      <div className="bg-background w-full max-w-md rounded-lg border p-6 shadow-2xl">
        <h2 id="session-expired-title" className="text-lg font-semibold">
          Your session expired
        </h2>
        <p className="text-muted-foreground mt-2 text-sm">
          Please sign in again to continue. Your unsaved work in this tab
          will be lost — copy any draft text before signing in.
        </p>
        <div className="mt-6 flex justify-end">
          <Button asChild>
            <a href={loginHref}>
              <LogInIcon className="size-4" />
              Sign in
            </a>
          </Button>
        </div>
      </div>
    </div>
  );
}
