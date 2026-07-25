"use client";

import {
  ActivityIcon,
  BoxesIcon,
  FileCodeIcon,
  GlobeIcon,
  KeyIcon,
  KeyRoundIcon,
  LayersIcon,
  RocketIcon,
  SettingsIcon,
  ShieldIcon,
  SlidersHorizontalIcon,
  UsersIcon,
  WebhookIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { cn } from "@/lib/utils";

// The underlying RegisteredApp's platform submenus. An agent IS an app, so
// these are the shared /apps/<slug>/<seg> clients — but each is mounted at a
// real /agents/<appSlug>/<seg> route (see agents/[agentSlug]/<seg>/page.tsx)
// that renders the same client inside the agent shell via AgentAppSurface, so
// the operator stays in agent context with no /apps template leakage. Kept as
// a flat leaf row (the agent already has the BROCS pillar bar above, so we
// don't repeat pillars).
const LINKS: { seg: string; label: string; Icon: typeof RocketIcon }[] = [
  { seg: "config", label: "Config", Icon: SlidersHorizontalIcon },
  { seg: "manifest", label: "Manifest", Icon: FileCodeIcon },
  { seg: "webhooks", label: "CI / CD", Icon: WebhookIcon },
  { seg: "deployments", label: "Deployments", Icon: RocketIcon },
  { seg: "environments", label: "Environments", Icon: LayersIcon },
  { seg: "domains", label: "Domains", Icon: GlobeIcon },
  { seg: "managed-services", label: "Services", Icon: BoxesIcon },
  { seg: "observability", label: "Observability", Icon: ActivityIcon },
  { seg: "settings", label: "Settings", Icon: SettingsIcon },
  { seg: "members", label: "Members", Icon: UsersIcon },
  { seg: "security", label: "Security", Icon: ShieldIcon },
  { seg: "secrets", label: "Secrets", Icon: KeyRoundIcon },
  { seg: "tokens", label: "Tokens", Icon: KeyIcon },
];

export function AppPlatformLinks({ appSlug }: { appSlug: string }) {
  const pathname = usePathname() ?? "";
  return (
    <div className="-mx-6 border-b">
      <div className="scrollbar-none flex items-center gap-1 overflow-x-auto px-6 py-1.5">
        <span className="text-muted-foreground/60 mr-1 shrink-0 text-2xs font-semibold uppercase tracking-wider">
          App
        </span>
        {LINKS.map(({ seg, label, Icon }) => {
          const href = `/agents/${appSlug}/${seg}`;
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
