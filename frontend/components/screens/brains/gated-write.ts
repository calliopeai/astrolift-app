import { waitForElevation } from "@/lib/auth/step-up-events";

/** A mutation's envelope as the brain writes return it. */
export type WriteEnvelope<T> =
  | {
      ok: boolean;
      errors: {
        code: string;
        message: string;
        field?: string | null;
        approvalRequestId?: string | null;
      }[];
      data?: T | null;
    }
  | null
  | undefined;

export type WriteOutcome<T> =
  | { ok: true; data: T }
  | {
      ok: false;
      code: string;
      message: string;
      field: string | null;
      /** Set when an action-safety rule asked for approval: the request to follow. */
      approvalRequestId: string | null;
    };

/**
 * One write that an action-safety gate may stop (#2202, #2424): a
 * `STEP_UP_REQUIRED` refusal has already opened the app-wide step-up prompt
 * (the Apollo afterware raises it), so this waits for the person to finish
 * it and sends the write once more. Cancelling, or any other refusal,
 * comes back as the server said it. `APPROVAL_REQUIRED` is a refusal too:
 * the server recorded a request, and the caller says where it is followed.
 */
export async function gatedWrite<T>(
  send: () => Promise<WriteEnvelope<T>>,
  fallback: string
): Promise<WriteOutcome<T>> {
  for (let attempt = 0; ; attempt++) {
    let envelope: WriteEnvelope<T>;
    try {
      envelope = await send();
    } catch (err) {
      return {
        ok: false,
        code: "NETWORK",
        message: err instanceof Error && err.message ? err.message : fallback,
        field: null,
        approvalRequestId: null,
      };
    }
    if (envelope?.ok && envelope.data) return { ok: true, data: envelope.data };
    const error = envelope?.errors?.[0];
    if (error?.code === "STEP_UP_REQUIRED" && attempt === 0 && (await waitForElevation())) continue;
    return {
      ok: false,
      code: error?.code ?? "UNKNOWN",
      message: error?.message || fallback,
      field: error?.field ?? null,
      approvalRequestId: error?.approvalRequestId ?? null,
    };
  }
}

/** Where approval requests are decided (`astrolift_identity.action_safety.APPROVALS_PATH`). */
export const APPROVALS_HREF = "/administration/permissions/safety";
