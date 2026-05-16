import { ExternalLinkIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";

interface EmptyStateProps {
  icon: React.ReactNode;
  title: string;
  description?: string;
  actionHref?: string;
  actionLabel?: string;
  /**
   * Deep link into the in-app `/resources/docs` (or another doc surface)
   * for context-sensitive help. When set, renders a subdued
   * "Learn more" link beneath the description.
   *
   * Pattern (ref #356): every empty state across the platform should
   * pass `learnMoreHref` pointing at the relevant resources page when
   * one exists. The link is intentionally low-prominence so it
   * supplements, rather than competes with, the primary CTA
   * (`actionHref` / `actionLabel`).
   *
   * External URLs (http/https) open in a new tab; in-app paths render
   * via next/link.
   */
  learnMoreHref?: string;
  learnMoreLabel?: string;
  secondary?: React.ReactNode;
}

export function EmptyState({
  icon,
  title,
  description,
  actionHref,
  actionLabel,
  learnMoreHref,
  learnMoreLabel,
  secondary,
}: EmptyStateProps) {
  const learnMoreText = learnMoreLabel ?? "Learn more";
  const isExternal =
    learnMoreHref?.startsWith("http://") ||
    learnMoreHref?.startsWith("https://");

  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-md border border-dashed py-12 px-6 text-center">
      <div className="bg-muted text-muted-foreground rounded-md p-2">{icon}</div>
      <div>
        <p className="font-medium">{title}</p>
        {description && (
          <p className="text-muted-foreground mx-auto mt-1 max-w-sm text-sm leading-relaxed">
            {description}
          </p>
        )}
        {learnMoreHref &&
          (isExternal ? (
            <a
              href={learnMoreHref}
              target="_blank"
              rel="noreferrer"
              className="text-muted-foreground hover:text-foreground mt-2 inline-flex items-center gap-1 text-xs underline-offset-2 hover:underline"
            >
              {learnMoreText}
              <ExternalLinkIcon className="size-3" />
            </a>
          ) : (
            <Link
              href={learnMoreHref}
              className="text-muted-foreground hover:text-foreground mt-2 inline-flex items-center gap-1 text-xs underline-offset-2 hover:underline"
            >
              {learnMoreText}
            </Link>
          ))}
      </div>
      {actionHref && actionLabel && (
        <Button asChild size="sm">
          <Link href={actionHref}>{actionLabel}</Link>
        </Button>
      )}
      {secondary}
    </div>
  );
}
