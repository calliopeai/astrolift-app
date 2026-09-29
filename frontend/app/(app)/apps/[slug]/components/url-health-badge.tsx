"use client";

import { UrlHealthBadgeView } from "@/components/screens/apps/overview/UrlHealthBadge";
import { useUrlHealth } from "@/components/screens/apps/overview/use-url-health";

interface Props {
  appSlug: string;
  url: string;
  /** Compact mode renders just the dot + latency, no status code. */
  compact?: boolean;
  className?: string;
}

/** Live HTTP health pill for an app URL (#406): the hook's data rendered by the view. */
export function UrlHealthBadge({ appSlug, url, compact, className }: Props) {
  return (
    <UrlHealthBadgeView
      {...useUrlHealth({ appSlug, url })}
      compact={compact}
      className={className}
    />
  );
}
