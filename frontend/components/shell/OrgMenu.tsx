"use client";

import { Building2Icon } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";

/**
 * The organization, heading the projects rail. One organization per
 * session today, so it names it; switching lands here when there are more.
 */
export function OrgMenu({ name, loading }: { name: string | null | undefined; loading?: boolean }) {
  if (loading) return <Skeleton className="h-5 w-32" />;
  return (
    <span
      className="flex min-w-0 items-center gap-2 text-sm font-semibold"
      title={name ?? undefined}
    >
      <Building2Icon className="text-muted-foreground size-4 shrink-0" aria-hidden />
      <span className="truncate">{name ?? "Control plane"}</span>
    </span>
  );
}
