"use client";

import {
  BookOpenIcon,
  FileTextIcon,
  LifeBuoyIcon,
  PlugIcon,
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
  {
    href: "/resources/docs",
    label: "Docs",
    icon: <BookOpenIcon className="size-4" />,
  },
  {
    href: "/resources/manifest",
    label: "Manifest reference",
    icon: <FileTextIcon className="size-4" />,
  },
  {
    href: "/resources/drivers",
    label: "Driver reference",
    icon: <PlugIcon className="size-4" />,
  },
  {
    href: "/resources/help",
    label: "Get help",
    icon: <LifeBuoyIcon className="size-4" />,
  },
];

export function ResourcesSubnav() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Resources sub-navigation"
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
