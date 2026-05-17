/**
 * Optimistic-concurrency client helpers (#497).
 *
 * When a high-value mutation (updateApp, setAppSecret, updatePolicy,
 * updateWebhookSubscription, updateIdentityProvider) detects a stale
 * ``ifMatchVersion``, the backend returns a structured envelope:
 *
 *   {
 *     ok: false,
 *     errors: [
 *       {
 *         code: "VERSION_MISMATCH",
 *         message: "App was modified by another session. Refresh and retry.",
 *         currentVersion: <int>,
 *         requestedVersion: <int>,
 *       },
 *     ],
 *   }
 *
 * The FE intercepts the envelope, raises a user-visible toast that
 * tells the operator their view is stale, and offers a "Refresh"
 * action that refetches the affected query so the next save sees the
 * latest persisted version.
 *
 * Two surfaces:
 *
 *  - :func:`isVersionMismatch` — predicate over the envelope shape.
 *  - :func:`handleVersionMismatch` — toast + refetch, returns true
 *    when the helper handled the error (caller short-circuits any
 *    generic error handling) and false on every other failure mode.
 */

import { toast } from "sonner";

export type MutationErrorLike = {
  code?: string | null;
  message?: string | null;
  currentVersion?: number | null;
  requestedVersion?: number | null;
};

export type MutationEnvelopeLike = {
  ok: boolean;
  errors?: MutationErrorLike[] | null;
};

export function isVersionMismatch(envelope: MutationEnvelopeLike | null | undefined): boolean {
  if (!envelope || envelope.ok) return false;
  const first = envelope.errors?.[0];
  return first?.code === "VERSION_MISMATCH";
}

/**
 * Show a "this changed under you" toast and offer a "Refresh" action
 * that calls the supplied refetcher (typically the same Apollo
 * ``refetch`` function attached to the query that read the entity).
 *
 * Returns true when the envelope was a version mismatch (handled);
 * false otherwise — caller falls through to its normal error path.
 */
export function handleVersionMismatch(
  envelope: MutationEnvelopeLike | null | undefined,
  options: {
    /** Human-readable label for the entity, e.g. "app", "policy". */
    label?: string;
    /** Called when the operator clicks Refresh. */
    onRefresh?: () => void | Promise<unknown>;
  } = {},
): boolean {
  if (!isVersionMismatch(envelope)) return false;
  const first = envelope?.errors?.[0];
  const message =
    first?.message ??
    `This ${options.label ?? "entity"} was modified by another session. Refresh and retry.`;
  if (options.onRefresh) {
    toast.error(message, {
      action: {
        label: "Refresh",
        onClick: () => {
          void options.onRefresh?.();
        },
      },
    });
  } else {
    toast.error(message);
  }
  return true;
}
