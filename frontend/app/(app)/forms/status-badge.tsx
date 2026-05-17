import type { ComponentProps } from "react";

import { Badge } from "@/components/ui/badge";

type BadgeProps = ComponentProps<typeof Badge>;

/**
 * Map a form status (draft / published / archived) to semantic shadcn
 * Badge variant props (#437 scope F).
 *
 * The previous renderer hardcoded ``bg-gray-500`` / ``bg-green-500`` /
 * ``bg-yellow-500`` which broke contrast in dark mode and bypassed the
 * theme's role tokens. Returning the variant — plus a tinted className
 * for ``published`` so success reads green in both themes — keeps the
 * Badge wrapper deciding the actual colors per theme.
 */
export function formStatusBadgeProps(status: string): BadgeProps {
  switch (status) {
    case "published":
      return {
        variant: "secondary",
        className:
          "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30",
      };
    case "archived":
      return { variant: "outline" };
    case "draft":
    default:
      return { variant: "secondary" };
  }
}
