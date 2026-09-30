"use client";

/**
 * Grace-period cancel state for DeregisterPendingBannerView (#436 B).
 *
 * Storage model: the deregister kickoff writes
 * `{workflowId, expiresAtMs}` to `localStorage` under
 * `astrolift.deregister.pending.<appSlug>`. This hook reads from
 * the same key. The window pins per app slug so re-opening a sibling
 * app's detail page doesn't show a stale banner.
 *
 * Hide rules:
 *   * Countdown elapsed (Date.now > expiresAtMs) — destructive work
 *     has started; cancel is no longer meaningful.
 *   * Cancel succeeded — banner clears immediately.
 *   * Stored entry missing or malformed — render nothing.
 */

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { CANCEL_DEREGISTER } from "@/graphql/lifecycle/lifecycle.mutations";

interface CancelResp {
  cancelAstroliftDeregister: MutationResult<{
    workflowId: string;
    signalDelivered: boolean;
  }>;
}

interface PendingEntry {
  workflowId: string;
  expiresAtMs: number;
}

// 5 min matches the backend grace window (#436 B). Mirroring the
// constant on the FE so the banner re-renders at one-second cadence
// against the same clock the backend uses to gate cancellation.
export const DEREGISTER_GRACE_MS = 5 * 60 * 1000;

const STORAGE_PREFIX = "astrolift.deregister.pending.";

export function deregisterPendingKey(appSlug: string): string {
  return `${STORAGE_PREFIX}${appSlug}`;
}

/** Write a pending entry. Called by the deregister mutation kickoff. */
export function recordDeregisterPending(appSlug: string, workflowId: string): void {
  if (typeof window === "undefined") return;
  const entry: PendingEntry = {
    workflowId,
    expiresAtMs: Date.now() + DEREGISTER_GRACE_MS,
  };
  try {
    window.localStorage.setItem(deregisterPendingKey(appSlug), JSON.stringify(entry));
    window.dispatchEvent(new Event("astrolift:deregister-pending"));
  } catch {
    // localStorage can throw under quota / private-mode constraints —
    // the banner is non-critical so swallow + fall through.
  }
}

/** Clear the pending entry. Called on cancel-success / window elapse. */
export function clearDeregisterPending(appSlug: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(deregisterPendingKey(appSlug));
    window.dispatchEvent(new Event("astrolift:deregister-pending"));
  } catch {
    // Same swallow as above — the banner self-corrects on next mount.
  }
}

function snapshot(appSlug: string): string {
  try {
    return window.localStorage.getItem(deregisterPendingKey(appSlug)) ?? "";
  } catch {
    return "";
  }
}
function readPending(raw: string): PendingEntry | null {
  try {
    const parsed = JSON.parse(raw) as Partial<PendingEntry>;
    return typeof parsed.workflowId === "string" &&
      typeof parsed.expiresAtMs === "number" &&
      Number.isFinite(parsed.expiresAtMs)
      ? { workflowId: parsed.workflowId, expiresAtMs: parsed.expiresAtMs }
      : null;
  } catch {
    return null;
  }
}
const emptySnapshot = () => "";

export function useDeregisterPending(appSlug: string) {
  const t = useTranslations("apps.dangerZone.pendingBanner");
  const subscribe = React.useCallback((notify: () => void) => {
    window.addEventListener("focus", notify);
    window.addEventListener("storage", notify);
    window.addEventListener("astrolift:deregister-pending", notify);
    return () => {
      window.removeEventListener("focus", notify);
      window.removeEventListener("storage", notify);
      window.removeEventListener("astrolift:deregister-pending", notify);
    };
  }, []);
  const getSnapshot = React.useCallback(() => snapshot(appSlug), [appSlug]);
  const raw = React.useSyncExternalStore(subscribe, getSnapshot, emptySnapshot);
  const entry = React.useMemo(() => readPending(raw), [raw]);
  const [now, setNow] = React.useState<number>(() => Date.now());
  const [cancel, { loading }] = useMutation<CancelResp>(CANCEL_DEREGISTER);

  // 1-second tick drives the countdown render. Stops once the entry
  // clears (cancel succeeded or window elapsed) so we don't burn a
  // background interval indefinitely.
  React.useEffect(() => {
    if (!entry) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [entry]);

  // Window elapsed → clear the entry so the next mount doesn't try to
  // cancel a workflow that's past its grace boundary.
  React.useEffect(() => {
    if (!entry) return;
    if (now >= entry.expiresAtMs) {
      clearDeregisterPending(appSlug);
    }
  }, [appSlug, entry, now]);

  async function onCancel() {
    if (!entry) return;
    try {
      const { data } = await cancel({
        variables: { input: { workflowId: entry.workflowId } },
      });
      const env = data?.cancelAstroliftDeregister;
      if (!env?.ok) {
        toast.error(env?.errors?.[0]?.message ?? t("toastError"));
        return;
      }
      const payload = env.data;
      if (payload?.signalDelivered === false) {
        // Backend reachable but Temporal couldn't deliver the signal
        // (workflow already finished, transport flake, dev no-op). The
        // operator should treat this as "didn't land" — we still clear
        // the banner so a stuck entry doesn't linger.
        toast.warning(t("toastNotDelivered"));
      } else {
        toast.success(t("toastCancelled"));
      }
      clearDeregisterPending(appSlug);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastError"));
    }
  }

  // null = nothing pending (no entry, or the window has elapsed).
  const msRemaining = entry && now < entry.expiresAtMs ? entry.expiresAtMs - now : null;

  return { msRemaining, cancelling: loading, onCancel };
}
