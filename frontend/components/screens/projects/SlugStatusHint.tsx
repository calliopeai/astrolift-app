import { AlertCircleIcon, CheckCircle2Icon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";

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
  const t = useTranslations("slugStatus");
  const scope = kind === "team" ? "organization" : "team";
  switch (status) {
    case "invalid":
      return (
        <p className="text-destructive flex items-center gap-1.5 text-xs">
          <AlertCircleIcon className="size-3.5" />
          {labels?.invalid ?? t("invalid")}
        </p>
      );
    case "checking":
      return (
        <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <Loader2Icon className="size-3.5 animate-spin" />
          {labels?.checking ?? t("checking")}
        </p>
      );
    case "available":
      return (
        <p className="text-success-fg flex items-center gap-1.5 text-xs">
          <CheckCircle2Icon className="size-3.5" />
          {labels?.available ?? t("available")}
        </p>
      );
    case "taken":
      return (
        <p className="text-destructive flex items-center gap-1.5 text-xs">
          <AlertCircleIcon className="size-3.5" />
          {labels?.taken ?? t("taken", { scope })}
        </p>
      );
    case "unchanged":
      return <p className="text-muted-foreground text-xs">{labels?.unchanged ?? t("unchanged")}</p>;
    default:
      return <p className="text-muted-foreground text-xs">{labels?.empty ?? t("empty")}</p>;
  }
}
