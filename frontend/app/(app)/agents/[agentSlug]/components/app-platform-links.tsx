"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

import { AGENT_PILLARS, agentBasePath, resolveActivePillar } from "./agent-nav-model";

/**
 * Secondary nav row for the agent shell: the ACTIVE pillar's platform
 * sub-pages, sitting under the `AgentTabs` pillar bar.
 *
 * An agent IS a RegisteredApp, so these are the shared `/apps/<slug>/<seg>`
 * clients, each mounted at a real `/agents/<agentSlug>/<seg>` route (see
 * `agents/[agentSlug]/<seg>/page.tsx` → `AgentAppSurface`) so the operator stays
 * in agent context with no `/apps` template leakage. They're grouped under the
 * agent's BROCS pillars with the same seg→pillar mapping the app surface uses
 * ({@link AGENT_PILLARS}) — so this row is scoped to the active pillar instead
 * of one identical flat list under every tab. Pillars with no platform
 * sub-pages (Overview) render nothing.
 */
export function AppPlatformLinks({ agentSlug }: { agentSlug: string }) {
  const pathname = usePathname() ?? "";
  const activePillar = resolveActivePillar(pathname, agentSlug);
  const pillar = AGENT_PILLARS.find((p) => p.key === activePillar);

  // Overview (and any pillar without platform pages) has no secondary row.
  if (!pillar || pillar.platform.length === 0) return null;

  const base = agentBasePath(agentSlug);

  return (
    <div className="-mx-6 border-b">
      <div className="scrollbar-none flex items-center gap-1 overflow-x-auto px-6 py-1.5">
        <span className="text-muted-foreground/60 mr-1 shrink-0 text-2xs font-semibold uppercase tracking-wider">
          App
        </span>
        {pillar.platform.map(({ seg, label, Icon }) => {
          const href = `${base}/${seg}`;
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={seg}
              href={href}
              className={cn(
                "inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors",
                active
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
              )}
            >
              <Icon className="size-3.5" />
              {label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
