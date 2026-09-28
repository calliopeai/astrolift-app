import type { ComponentProps } from "react";

import { Badge } from "@/components/ui/badge";

type BadgeProps = ComponentProps<typeof Badge>;

/**
 * Map a form status (draft / published / archived) to semantic shadcn
 * Badge variant props (#437 scope F).
 *
 * The previous renderer hardcoded ``bg-gray-500`` / ``bg-success`` /
 * ``bg-warning`` which broke contrast in dark mode and bypassed the
 * theme's role tokens. Returning the variant — plus a tinted className
 * for ``published`` so success reads green in both themes — keeps the
 * Badge wrapper deciding the actual colors per theme.
 */
export function formStatusBadgeProps(status: string): BadgeProps {
  switch (status) {
    case "published":
      return {
        variant: "secondary",
        className: "bg-success/15 text-success-fg border-success-border",
      };
    case "archived":
      return { variant: "outline" };
    case "draft":
    default:
      return { variant: "secondary" };
  }
}
