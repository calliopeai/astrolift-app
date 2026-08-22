import { AlertTriangleIcon, FileWarningIcon } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * Surfaces a deploy that rendered the *stored* manifest rather than the
 * repo's current one (#1553).
 *
 * Every deploy re-fetches `astrolift.toml` first (#1535), but that refresh is
 * best-effort: it refuses when an unpushed staged draft would be clobbered
 * (`diverged`), and it gives up when the file can't be read (`fetch_failed`).
 * Both cases used to exist only as a worker log line, so "I pushed a fix and
 * redeployed and nothing changed" looked like the platform caching the
 * manifest forever. Each status gets the specific remedy, because the two
 * need opposite actions from the operator.
 */
export function StaleManifestNotice({
  status,
  error,
  appSlug,
  className,
}: {
  status: string | null | undefined;
  error: string | null | undefined;
  appSlug: string;
  className?: string;
}) {
  // applied / in_sync are the healthy outcomes; empty means the deploy
  // predates the field or the app has no source repo to resync from.
  if (status !== "diverged" && status !== "fetch_failed") return null;

  const diverged = status === "diverged";
  const Icon = diverged ? FileWarningIcon : AlertTriangleIcon;

  return (
    <div
      className={cn(
        "border-warning-border bg-warning-bg text-warning-fg flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm",
        className,
      )}
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 space-y-1">
        <p className="font-semibold">
          {diverged
            ? "This deploy used the saved manifest, not your repo"
            : "This deploy used the last known manifest"}
        </p>
        <p className="text-foreground/80">
          {diverged ? (
            <>
              An unsaved draft of <code className="font-mono text-xs">astrolift.toml</code> is
              held on the platform, so the copy in your repo was left alone to avoid discarding
              it. Push or discard the draft, then deploy again.
            </>
          ) : (
            <>
              The manifest couldn&apos;t be read from your repo, so the previous one was used.
              Anything you pushed since the last successful read is not in this rollout.
            </>
          )}
        </p>
        {error && <p className="text-muted-foreground font-mono text-2xs break-words">{error}</p>}
        <Link
          href={`/apps/${appSlug}/manifest`}
          className="text-primary inline-block font-medium underline-offset-4 hover:underline"
        >
          {diverged ? "Review the draft" : "Open the manifest"} →
        </Link>
      </div>
    </div>
  );
}
