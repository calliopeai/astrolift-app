"use client";

import Image from "next/image";
import Link from "next/link";

import { cn } from "@/lib/utils";

/** The Astrolift mark, heading the main rail; the mark alone when collapsed. */
export function BrandMark({
  collapsed = false,
  href = "/dashboard",
}: {
  collapsed?: boolean;
  href?: string;
}) {
  return (
    <Link
      href={href}
      aria-label="Astrolift home"
      className={cn(
        "focus-visible:ring-ring flex min-w-0 items-center gap-2 rounded-md focus-visible:ring-2 focus-visible:outline-none",
        collapsed && "justify-center"
      )}
    >
      <span className="bg-sidebar-primary/10 ring-sidebar-primary/30 flex size-7 shrink-0 items-center justify-center rounded-md ring-1">
        <Image src="/logo.svg" alt="" width={18} height={18} priority />
      </span>
      {!collapsed && (
        <span className="font-head truncate text-sm font-semibold tracking-tight">Astrolift</span>
      )}
    </Link>
  );
}
