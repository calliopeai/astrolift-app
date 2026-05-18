/**
 * Step-up auth client-side wiring (#487).
 *
 * Defines the custom-event channel the Apollo afterware and the
 * StepUpPrompt modal use to talk to each other:
 *
 *   Apollo response → detect ``STEP_UP_REQUIRED`` envelope
 *                  → dispatch ``astrolift:step-up-required``
 *   StepUpPrompt   → listen, open modal, run elevateAdminSession
 *                  → on success, dispatch ``astrolift:elevated``
 *                  → callers that opted into ``waitForElevation``
 *                    re-fire their original mutation.
 *
 * Centralising the event name + payload shape here keeps the modal
 * and afterware in lock-step — TypeScript will surface a drift if
 * either side touches the shape without updating the other.
 */

export const STEP_UP_EVENT = "astrolift:step-up-required" as const;
export const ELEVATED_EVENT = "astrolift:elevated" as const;
export const DEELEVATED_EVENT = "astrolift:deelevated" as const;

/**
 * Credential families the FE can use to satisfy step-up. Mirrors the
 * backend's ``METHOD_*`` constants in
 * ``astrolift_identity.session_elevation``. The deny envelope from a
 * ``@requires_elevation``-gated mutation lists which one(s) apply to
 * the current session — SSO-only installs surface ``"sso"``,
 * local-login installs surface ``"password"`` (#526).
 */
export type StepUpMethod = "password" | "sso" | "webauthn" | "magic_link";

export type StepUpEventDetail = {
  operationName: string;
  message: string;
  /**
   * Credential families the current session can use to satisfy this
   * step-up gate (#526). Undefined for legacy denies issued before
   * the backend started populating ``supported_methods`` — the modal
   * falls back to the password form in that case.
   */
  supportedMethods?: StepUpMethod[];
};

export type ElevatedEventDetail = {
  elevatedUntil: string; // ISO 8601
  secondsRemaining: number;
  method: string;
};

/**
 * Wait for the next step-up-success event. Returns true if the
 * operator elevated; false if they cancelled (modal dispatches the
 * cancellation as a deelevated event so callers know not to retry).
 *
 * Calls that opt into ``waitForElevation`` should re-issue the
 * original mutation after the await resolves; the Apollo afterware
 * does not retry automatically because the caller's variables /
 * loading state must drive the retry, not a side-channel.
 */
export function waitForElevation(): Promise<boolean> {
  return new Promise((resolve) => {
    if (typeof window === "undefined") {
      resolve(false);
      return;
    }
    function onElevated() {
      cleanup();
      resolve(true);
    }
    function onDeelevated() {
      cleanup();
      resolve(false);
    }
    function cleanup() {
      window.removeEventListener(ELEVATED_EVENT, onElevated);
      window.removeEventListener(DEELEVATED_EVENT, onDeelevated);
    }
    window.addEventListener(ELEVATED_EVENT, onElevated, { once: true });
    window.addEventListener(DEELEVATED_EVENT, onDeelevated, { once: true });
  });
}
