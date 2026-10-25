import type { BrainConnection } from "./brain-connection";

const LOGIN = "/__astrolift/brain-browser/login";
const NONCE = /^[a-f0-9]{64}$/;
const MAX_TOKEN = 3500;

export function brainBrowserOrigin(endpoint: string): string {
  const url = new URL(endpoint);
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    !["", "/"].includes(url.pathname) ||
    url.search ||
    url.hash
  ) {
    throw new Error("Brain browser connection unavailable");
  }
  return url.origin;
}

function closedMessage(value: unknown): value is {
  kind: string;
  nonce: string;
  brainId: string;
  returnPath: string;
} {
  return (
    typeof value === "object" &&
    value !== null &&
    Object.keys(value).sort().join(",") === "brainId,kind,nonce,returnPath" &&
    typeof (value as Record<string, unknown>).kind === "string" &&
    typeof (value as Record<string, unknown>).nonce === "string" &&
    NONCE.test((value as { nonce: string }).nonce)
  );
}

/** One explicit browser Connect; no token URL, storage, cache or background refresh. */
export function openBrainBrowser(
  selection: {
    brainId: string;
    slug: string;
    endpoint: string;
    issue: (signal: AbortSignal) => Promise<BrainConnection>;
    signal?: AbortSignal;
  },
  view: Window = window
): Promise<void> {
  if (selection.signal?.aborted)
    return Promise.reject(new Error("Brain browser connection unavailable"));
  const target = brainBrowserOrigin(selection.endpoint);
  if (target === view.location.origin) {
    return Promise.reject(new Error("Brain browser connection unavailable"));
  }
  const login = new URL(LOGIN, target);
  login.searchParams.set("return", "/");
  return new Promise((resolve, reject) => {
    let popup: Window | null = null;
    let phase: "waiting" | "issuing" | "sent" | "done" = "waiting";
    let nonce: string | null = null;
    const abort = new AbortController();
    let timeout: number | undefined;
    let closed: number | undefined;
    const finish = (success: boolean) => {
      if (phase === "done") return;
      phase = "done";
      view.removeEventListener("message", receive);
      selection.signal?.removeEventListener("abort", cancel);
      view.clearTimeout(timeout);
      view.clearInterval(closed);
      abort.abort();
      if (success) resolve();
      else {
        popup?.close();
        reject(new Error("Brain browser connection unavailable"));
      }
    };
    const cancel = () => finish(false);
    const receive = async (event: MessageEvent<unknown>) => {
      if (!popup || event.origin !== target || event.source !== popup || !closedMessage(event.data))
        return;
      const value = event.data;
      if (value.brainId !== selection.brainId || value.returnPath !== "/") return;
      if (phase === "sent") {
        if (value.kind === "astrolift-brain-connected/v1" && value.nonce === nonce) finish(true);
        return;
      }
      if (phase !== "waiting" || value.kind !== "astrolift-brain-ready/v1") return;
      phase = "issuing";
      nonce = value.nonce;
      try {
        // The existing authenticated APP_READ mutation runs only after the exact
        // target popup has passed its normal edge OIDC and produced its nonce.
        const connection = await selection.issue(abort.signal);
        if (phase !== "issuing") return;
        if (
          connection.id !== selection.brainId ||
          connection.slug !== selection.slug ||
          connection.header !== "X-Astrolift-Brain-Token" ||
          !connection.endpoints.some((endpoint) => brainBrowserOrigin(endpoint) === target) ||
          !Number.isFinite(Date.parse(connection.expiresAt)) ||
          Date.parse(connection.expiresAt) <= Date.now() + 5000 ||
          connection.token.length > MAX_TOKEN ||
          !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(connection.token) ||
          popup?.closed
        ) {
          finish(false);
          return;
        }
        phase = "sent";
        popup?.postMessage(
          { kind: "astrolift-brain-token/v1", nonce, token: connection.token },
          target
        );
      } catch {
        finish(false);
      }
    };
    view.addEventListener("message", receive);
    selection.signal?.addEventListener("abort", cancel, { once: true });
    timeout = view.setTimeout(() => finish(false), 120_000);
    closed = view.setInterval(() => {
      if (popup?.closed) finish(false);
    }, 250);
    // Keep the opener only for this exact-origin/WindowProxy handshake. The
    // host clears it before navigating, after the one-use token POST succeeds.
    try {
      popup = view.open(login.toString(), "_blank", "popup,width=800,height=700");
      if (!popup) finish(false);
    } catch {
      finish(false);
    }
  });
}
