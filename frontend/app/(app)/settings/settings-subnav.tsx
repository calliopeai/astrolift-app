"use client";

import {
  BuildingIcon,
  GitBranchIcon,
  KeyRoundIcon,
  ScaleIcon,
  ShieldCheckIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { cn } from "@/lib/utils";

interface SubnavLink {
  href: string;
  label: string;
  icon: React.ReactNode;
}

const LINKS: SubnavLink[] = [
  { href: "/settings/organization", label: "Organization", icon: <BuildingIcon className="size-4" /> },
  { href: "/settings/identity-provider", label: "Identity provider", icon: <KeyRoundIcon className="size-4" /> },
  { href: "/settings/source-providers", label: "Source providers", icon: <GitBranchIcon className="size-4" /> },
  { href: "/settings/policies", label: "Policies", icon: <ScaleIcon className="size-4" /> },
  { href: "/settings/permissions", label: "Permissions", icon: <ShieldCheckIcon className="size-4" /> },
];

export function SettingsSubnav() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Settings sub-navigation"
      className="border-border bg-muted/30 sticky top-0 z-10 flex flex-wrap items-center gap-1 border-b px-4 py-2 backdrop-blur"
    >
      {LINKS.map((link) => {
        const active =
          pathname === link.href || pathname.startsWith(link.href + "/");
        return (
          <Link
            key={link.href}
            href={link.href}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm transition-colors",
              active
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            aria-current={active ? "page" : undefined}
          >
            {link.icon}
            <span>{link.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
