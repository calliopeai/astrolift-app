const TOKEN_KEY = "jwt";
export const SESSION_CHANGED_EVENT = "astrolift:session-changed";
let sessionEpoch = 0;

// In dev-login mode the browser authenticates via Django's sessionid
// cookie (set by /app/auth1/dev-login), not via a backend_jwt. If a
// stale JWT from a prior session is sitting in localStorage, sending
// it as Authorization: <token> trips Auth0SessionMiddleware's
// Bearer-token check and every GraphQL request 401s — even though
// the sessionid cookie alone would authenticate fine. Skip the JWT
// lookup entirely in dev-login mode and let the cookie do its job.
const DEV_LOGIN = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";

// A 401 from getToken means this page has no session to find. Asking again
// on every request only repeats it, one round trip per GraphQL operation, so
// the answer holds until a sign-in stores a token (a new page load).
let noSession = false;
let pending: Promise<string | null> | null = null;

async function requestToken(): Promise<string | null> {
  try {
    const res = await fetch("/api/session/getToken", { method: "POST" });
    if (res.status === 401) {
      noSession = true;
      return null;
    }
    if (!res.ok) return null;
    const data = await res.json();
    const token: string | null = data.Authorization ?? null;
    if (token) setClientToken(token);
    return token;
  } catch {
    return null;
  }
}

export async function getClientToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  if (DEV_LOGIN) return null;

  const stored = localStorage.getItem(TOKEN_KEY);
  if (stored && stored !== "undefined" && stored !== "null") return stored;
  if (noSession) return null;

  // Requests issued together share one lookup.
  pending ??= requestToken().finally(() => {
    pending = null;
  });
  return pending;
}

export function clearToken(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem(TOKEN_KEY);
    sessionEpoch++;
    window.dispatchEvent(new Event(SESSION_CHANGED_EVENT));
  }
}

export function setClientToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  noSession = false;
  sessionEpoch++;
  window.dispatchEvent(new Event(SESSION_CHANGED_EVENT));
}

/** A disclosure fence, never an authorization decision or decoded-claim check. */
export function captureClientSession(): (() => boolean) | null {
  if (typeof window === "undefined" || DEV_LOGIN) return null;
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token || token === "undefined" || token === "null") return null;
  const epoch = sessionEpoch;
  return () => sessionEpoch === epoch && localStorage.getItem(TOKEN_KEY) === token;
}

export function watchClientSessionChanges(changed: () => void): () => void {
  const storage = (event: StorageEvent) => {
    if (event.key === null || event.key === TOKEN_KEY) changed();
  };
  window.addEventListener(SESSION_CHANGED_EVENT, changed);
  window.addEventListener("astrolift:session-expired", changed);
  window.addEventListener("storage", storage);
  return () => {
    window.removeEventListener(SESSION_CHANGED_EVENT, changed);
    window.removeEventListener("astrolift:session-expired", changed);
    window.removeEventListener("storage", storage);
  };
}
