"use client";

import { CopyIcon, MoreHorizontalIcon } from "lucide-react";
import Link from "next/link";
import type * as React from "react";
import { toast } from "sonner";

import type { EmptyStateSpec } from "@/components/data-table";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { type Crumb, ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/** A run detail's ⋯: its links, then Copy run ID. */
export function RunMenu({ id, links }: { id: string; links: { label: string; href: string }[] }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="size-8" aria-label="More actions">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-44">
        {links.map((l) => (
          <DropdownMenuItem key={l.href} asChild>
            <Link href={l.href}>{l.label}</Link>
          </DropdownMenuItem>
        ))}
        <DropdownMenuItem
          onSelect={() => {
            navigator.clipboard
              .writeText(id)
              .then(() => toast.success("Run ID copied."))
              .catch(() => toast.error("Couldn't copy to clipboard."));
          }}
        >
          <CopyIcon className="size-4" />
          Copy run ID
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** No run to show: it failed to load (with Retry) or does not exist. */
export function RunMissing({
  crumbs,
  title,
  icon,
  error,
  onRetry,
  empty,
}: {
  crumbs: Crumb[];
  title: string;
  icon: React.ReactNode;
  error: { message: string } | null;
  onRetry: () => void;
  empty: EmptyStateSpec;
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader crumbs={crumbs} title={title} />
      <PanelGrid>
        <Panel
          title={title}
          icon={icon}
          error={error}
          onRetry={onRetry}
          empty={error ? null : empty}
        />
      </PanelGrid>
    </div>
  );
}
