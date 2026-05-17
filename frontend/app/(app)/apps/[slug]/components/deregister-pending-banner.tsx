"use client";

/**
 * DeregisterPendingBanner (#436 B) — grace-period cancel surface.
 *
 * Pinned banner that appears at the top of the app detail page after a
 * successful deregister kickoff. Renders a live countdown of the 5-min
 * grace window during which the operator can cancel a typo'd confirm
 * before any destructive activity runs.
 *
 * Storage model: the deregister kickoff writes
 * `{workflowId, expiresAtMs}` to `localStorage` under
 * `astrolift.deregister.pending.<appSlug>`. This component reads from
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
import { CircleSlashIcon, Loader2Icon, ShieldAlertIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
  } catch {
    // Same swallow as above — the banner self-corrects on next mount.
  }
}

function readPending(appSlug: string): PendingEntry | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(deregisterPendingKey(appSlug));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PendingEntry>;
    if (
      typeof parsed.workflowId !== "string" ||
      typeof parsed.expiresAtMs !== "number" ||
      !Number.isFinite(parsed.expiresAtMs)
    ) {
      return null;
    }
    return { workflowId: parsed.workflowId, expiresAtMs: parsed.expiresAtMs };
  } catch {
    return null;
  }
}

function formatCountdown(msRemaining: number): string {
  if (msRemaining <= 0) return "0:00";
  const totalSeconds = Math.ceil(msRemaining / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export function DeregisterPendingBanner({ appSlug }: { appSlug: string }) {
  const t = useTranslations("apps.dangerZone.pendingBanner");
  const [entry, setEntry] = React.useState<PendingEntry | null>(null);
  const [now, setNow] = React.useState<number>(() => Date.now());
  const [cancel, { loading }] = useMutation<CancelResp>(CANCEL_DEREGISTER);

  // On mount + on tab focus, re-read the localStorage entry. The focus
  // listener catches the cross-tab case where the operator kicked off
  // the deregister on Settings and then switched to a tab pinned on
  // app detail — the banner appears as soon as that tab gains focus.
  React.useEffect(() => {
    const sync = () => setEntry(readPending(appSlug));
    sync();
    window.addEventListener("focus", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("focus", sync);
      window.removeEventListener("storage", sync);
    };
  }, [appSlug]);

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
      setEntry(null);
    }
  }, [appSlug, entry, now]);

  if (!entry) return null;
  const msRemaining = Math.max(0, entry.expiresAtMs - now);
  if (msRemaining <= 0) return null;

  async function handleCancel() {
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
      setEntry(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastError"));
    }
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-3 rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-amber-900 dark:text-amber-100"
    >
      <ShieldAlertIcon className="size-5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{t("title")}</p>
        <p className="text-xs opacity-80">
          {t("body", { countdown: formatCountdown(msRemaining) })}
        </p>
      </div>
      <Button
        size="sm"
        variant="outline"
        className="border-amber-600/40 hover:bg-amber-500/20"
        onClick={() => void handleCancel()}
        disabled={loading}
      >
        {loading ? (
          <Loader2Icon className="size-4 animate-spin" />
        ) : (
          <CircleSlashIcon className="size-4" />
        )}
        {t("cancel")}
      </Button>
    </div>
  );
}
