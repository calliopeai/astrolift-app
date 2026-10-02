export const SECRET_PROPOSALS_CHANGED = "astrolift-secret-proposals-changed";

export function invalidateSecretProposalQueue() {
  window.dispatchEvent(new Event(SECRET_PROPOSALS_CHANGED));
}
