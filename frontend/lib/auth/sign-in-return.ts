const DEVICE_APPROVAL_PATH = /^\/app\/cli\/auth\/device\/[A-Za-z0-9_-]+\/$/;
const INVITATION_PATH = /^\/auth\/invitation\/[A-Za-z0-9_-]+$/;

/**
 * Where a sign-in may return to: a CLI approval page or an invitation, by
 * path on this origin with no query or fragment. Anything else is the
 * dashboard, so a crafted `next` cannot send the browser elsewhere.
 */
export function signInReturnPath(value: string | null): string {
  if (!value?.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return "/dashboard";
  }
  try {
    const url = new URL(value, "https://astrolift.invalid");
    if (
      url.origin === "https://astrolift.invalid" &&
      (DEVICE_APPROVAL_PATH.test(url.pathname) || INVITATION_PATH.test(url.pathname)) &&
      !url.search &&
      !url.hash
    ) {
      return url.pathname;
    }
  } catch {
    // An invalid return address falls back to the ordinary dashboard.
  }
  return "/dashboard";
}

/** The invitation page's own path, as a sign-in return address. */
export function invitationPath(token: string): string {
  return `/auth/invitation/${encodeURIComponent(token)}`;
}

export function authCallbackUrl(origin: string, returnPath: string): string {
  const callback = new URL("/auth/callback", origin);
  if (returnPath !== "/dashboard") callback.searchParams.set("next", returnPath);
  return callback.toString();
}
