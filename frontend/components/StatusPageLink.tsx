"use client";

/**
 * Footer link to the platform's public status page (#702).
 *
 * When the deployment configures `NEXT_PUBLIC_STATUS_PAGE_URL` at
 * build time, this renders a small inline link in the sidebar
 * footer so operators can self-serve "is the platform degraded"
 * without leaving the app. When the env var is unset (most local
 * dev), the component renders nothing so the footer stays tight.
 *
 * The "consume a live status feed + render a banner when incidents
 * are open" half of #702 is filed as a follow-up — that needs a
 * feed protocol (Atom / Statuspage.io API / RSS / JSON) which is a
 * platform-deployment decision rather than a UI one.
 */

import { ActivityIcon } from "lucide-react";

export function StatusPageLink({
  url = process.env.NEXT_PUBLIC_STATUS_PAGE_URL,
}: {
  /** Defaults to the build's NEXT_PUBLIC_STATUS_PAGE_URL; passed in by stories. */
  url?: string;
} = {}) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-muted-foreground hover:text-foreground flex items-center gap-2 px-2 py-1 text-xs"
    >
      <ActivityIcon className="size-3.5 shrink-0" aria-hidden />
      <span>Platform status</span>
    </a>
  );
}
