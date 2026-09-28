"use client";

import { BookOpenIcon, BotIcon, WrenchIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { cn } from "@/lib/utils";

interface SubnavLink {
  href: string;
  label: string;
  icon: React.ReactNode;
  /** Exact-match only (so "/agents" doesn't stay active on child routes). */
  exact?: boolean;
}

const LINKS: SubnavLink[] = [
  { href: "/agents", label: "Agents", icon: <BotIcon className="size-4" />, exact: true },
  { href: "/agents/skills", label: "Skills", icon: <BookOpenIcon className="size-4" /> },
  { href: "/agents/tools", label: "Tools", icon: <WrenchIcon className="size-4" /> },
];

export interface AgentsSubnavViewProps {
  /** The current pathname; the subnav renders only on the section surfaces. */
  pathname: string;
}

/**
 * Section subnav that makes Skills and Tools reachable as tabs of Agents
 * rather than standalone sidebar entries (#915). Shown on the Agents list and
 * the Skills/Tools surfaces; hidden on an agent's detail (`/agents/<slug>`),
 * which carries its own BROCS shell.
 */
export function AgentsSubnavView({ pathname }: AgentsSubnavViewProps) {
  const onSectionSurface =
    pathname === "/agents" ||
    pathname.startsWith("/agents/skills") ||
    pathname.startsWith("/agents/tools");
  if (!onSectionSurface) return null;

  return (
    <nav
      aria-label="Agents sub-navigation"
      className="border-border bg-muted/30 sticky top-0 z-10 flex flex-wrap items-center gap-1 border-b px-4 py-2 backdrop-blur"
    >
      {LINKS.map((link) => {
        const active = link.exact
          ? pathname === link.href
          : pathname === link.href || pathname.startsWith(link.href + "/");
        return (
          <Link
            key={link.href}
            href={link.href}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm transition-colors",
              active
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {link.icon}
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
