import type { WizardState } from "./wizard-client";

// Every marker the server writes in place of an env value, an env comment,
// or an unreadable manifest the viewer can't reveal starts with this (#1948).
const MASKED_MARKER = "[ASTROLIFT_REDACTED_";

export function hasMaskedEnvValues(manifestRaw: string): boolean {
  return manifestRaw.includes(MASKED_MARKER);
}

/**
 * The `manifestRaw` registerApp gets. Null registers without an inline
 * manifest, and the server reads the repo file itself. That covers "Set up
 * manifest later" (#1172) and a manifest loaded from the repo with env values
 * masked for this viewer (#1948): sending that text would store the
 * placeholders, and the server refuses it. Edited text is sent as typed, so
 * any placeholder left in it is refused there.
 */
export function manifestRawForSubmit(
  state: Pick<WizardState, "manifestLater" | "manifestFromRepo" | "manifestRaw">
): string | null {
  if (state.manifestLater) return null;
  if (state.manifestFromRepo && hasMaskedEnvValues(state.manifestRaw)) return null;
  return state.manifestRaw.trim() || null;
}
