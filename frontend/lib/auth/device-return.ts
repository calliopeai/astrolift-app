const DEVICE_APPROVAL_PATH = /^\/app\/cli\/auth\/device\/[A-Za-z0-9_-]+\/$/;

export function deviceApprovalReturnPath(value: string | null): string {
  if (!value?.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return "/dashboard";
  }
  try {
    const url = new URL(value, "https://astrolift.invalid");
    if (
      url.origin === "https://astrolift.invalid" &&
      DEVICE_APPROVAL_PATH.test(url.pathname) &&
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

export function authCallbackUrl(origin: string, returnPath: string): string {
  const callback = new URL("/auth/callback", origin);
  if (returnPath !== "/dashboard") callback.searchParams.set("next", returnPath);
  return callback.toString();
}
