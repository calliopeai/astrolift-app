import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";

/** A workflow run's state as a badge, coloured by how the state reads. */
export function RunStateBadge({ state }: { state: string }) {
  const t = useTranslations("workflowRun");
  const s = state.toLowerCase();
  const variant: "default" | "secondary" | "destructive" | "outline" =
    s.includes("fail") || s.includes("error") || s.includes("terminat")
      ? "destructive"
      : s.includes("complete") || s.includes("succe") || s.includes("done")
        ? "secondary"
        : s.includes("cancel") || s === "expired"
          ? "outline"
          : "default";
  return <Badge variant={variant}>{s === "expired" ? t("historyExpired") : state}</Badge>;
}
