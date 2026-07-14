"use client";

import {
  BarChart3Icon,
  CoinsIcon,
  FileBoxIcon,
  GaugeIcon,
  KeyIcon,
  ShieldIcon,
  UsersIcon,
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
  { href: "/administration/teams", label: "Teams", icon: <UsersIcon className="size-4" /> },
  { href: "/administration/projects", label: "Projects", icon: <FileBoxIcon className="size-4" /> },
  { href: "/administration/members", label: "Members", icon: <ShieldIcon className="size-4" /> },
  { href: "/tokens", label: "Tokens", icon: <KeyIcon className="size-4" /> },
  { href: "/administration/cost", label: "Cost", icon: <CoinsIcon className="size-4" /> },
  { href: "/administration/quotas", label: "Quotas", icon: <GaugeIcon className="size-4" /> },
  { href: "/administration/metrics", label: "Metrics", icon: <BarChart3Icon className="size-4" /> },
];

export function AdministrationSubnav() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Administration sub-navigation"
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
