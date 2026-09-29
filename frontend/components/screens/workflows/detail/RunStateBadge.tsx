import { Badge } from "@/components/ui/badge";

/** A workflow run's state as a badge, coloured by how the state reads. */
export function RunStateBadge({ state }: { state: string }) {
  const s = state.toLowerCase();
  const variant: "default" | "secondary" | "destructive" | "outline" =
    s.includes("fail") || s.includes("error") || s.includes("terminat")
      ? "destructive"
      : s.includes("complete") || s.includes("succe") || s.includes("done")
        ? "secondary"
        : s.includes("cancel")
          ? "outline"
          : "default";
  return <Badge variant={variant}>{state}</Badge>;
}
