"use client";

import { Badge } from "@/components/ui/badge";

/**
 * The signed-in user as a badge. Pure (Storybook first). Imported nowhere
 * today: on the cut list for the migration's last phase (spec 44 §9).
 */
export default function AppUser({ name }: { name: string | null | undefined }) {
  if (!name) return null;
  return <Badge variant="secondary">{name}</Badge>;
}
