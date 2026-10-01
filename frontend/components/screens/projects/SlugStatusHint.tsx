import { AlertCircleIcon, CheckCircle2Icon, Loader2Icon } from "lucide-react";

import type { SlugStatus } from "./project-team-slug";

/**
 * Inline valid / taken / checking indicator rendered under the slug input
 * of the project and team edit sheets, so the operator sees availability
 * as they type.
 */
export function SlugStatusHint({
  status,
  kind,
  labels,
}: {
  status: SlugStatus;
  kind: "team" | "project";
  labels?: Record<SlugStatus, string>;
}) {
  const scope = kind === "team" ? "organization" : "team";
  switch (status) {
    case "invalid":
      return (
        <p className="text-destructive flex items-center gap-1.5 text-xs">
          <AlertCircleIcon className="size-3.5" />
          {labels?.invalid ??
            "Lowercase letters, numbers, and hyphens; must start with a letter (max 40)."}
        </p>
      );
    case "checking":
      return (
        <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <Loader2Icon className="size-3.5 animate-spin" />
          {labels?.checking ?? "Checking availability…"}
        </p>
      );
    case "available":
      return (
        <p className="text-success-fg flex items-center gap-1.5 text-xs">
          <CheckCircle2Icon className="size-3.5" />
          {labels?.available ?? "Available"}
        </p>
      );
    case "taken":
      return (
        <p className="text-destructive flex items-center gap-1.5 text-xs">
          <AlertCircleIcon className="size-3.5" />
          {labels?.taken ?? `Already taken in this ${scope}.`}
        </p>
      );
    case "unchanged":
      return (
        <p className="text-muted-foreground text-xs">
          {labels?.unchanged ?? "Current slug — used in URLs."}
        </p>
      );
    default:
      return (
        <p className="text-muted-foreground text-xs">
          {labels?.empty ?? "Lowercase letters, numbers, hyphens. Used in URLs."}
        </p>
      );
  }
}
