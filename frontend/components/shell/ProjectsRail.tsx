"use client";

import {
  BotIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ChevronsLeftIcon,
  ChevronsRightIcon,
  FolderIcon,
  PackageIcon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { StatusDot } from "@/components/StatusDot";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * The projects rail: the organization and the projects this person is in, on
 * the right (spec 44 §4.2). Pure: the tree, the org and the collapsed state
 * are the caller's. It navigates; it never filters the main lists (§10.13).
 */

export type ProjectEntityKind = "agent" | "app" | "workflow";

export interface ProjectEntity {
  kind: ProjectEntityKind;
  key: string;
  name: string;
  href: string;
  status?: "ok" | "warn" | "error" | "pending" | "muted";
}

export interface ProjectNode {
  slug: string;
  name: string;
  entities: ProjectEntity[];
}

export interface ProjectsRailProps {
  /** The organization switcher, heading the rail. */
  org: React.ReactNode;
  projects: ProjectNode[];
  loading?: boolean;
  /** The entity on screen, highlighted in the tree; its project opens. */
  activeHref?: string;
  allProjectsHref: string;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  className?: string;
}

const KIND_ICON = { agent: BotIcon, app: PackageIcon, workflow: WorkflowIcon } as const;

export function ProjectsRail({
  org,
  projects,
  loading,
  activeHref,
  allProjectsHref,
  collapsed,
  onCollapsedChange,
  className,
}: ProjectsRailProps) {
  if (collapsed) {
    return (
      <aside
        aria-label="Projects"
        className={cn(
          "bg-sidebar border-sidebar-border flex h-full w-8 shrink-0 flex-col items-center border-l py-2",
          className
        )}
      >
        <button
          type="button"
          onClick={() => onCollapsedChange(false)}
          aria-label="Show your projects"
          title="Projects  ]"
          className="text-muted-foreground hover:text-foreground hover:bg-sidebar-accent focus-visible:ring-ring flex size-7 items-center justify-center rounded-md focus-visible:ring-2 focus-visible:outline-none"
        >
          <ChevronsLeftIcon className="size-4" />
        </button>
        <FolderIcon className="text-muted-foreground mt-2 size-4" aria-hidden />
      </aside>
    );
  }

  return (
    <aside
      aria-label="Projects"
      className={cn(
        "bg-sidebar text-sidebar-foreground border-sidebar-border flex h-full w-[260px] shrink-0 flex-col border-l",
        className
      )}
    >
      <div className="border-sidebar-border flex min-w-0 items-center gap-2 border-b px-3 py-2">
        <div className="min-w-0 flex-1">{org}</div>
        <button
          type="button"
          onClick={() => onCollapsedChange(true)}
          aria-label="Hide your projects"
          title="Hide  ]"
          className="text-muted-foreground hover:text-foreground hover:bg-sidebar-accent focus-visible:ring-ring flex size-7 shrink-0 items-center justify-center rounded-md focus-visible:ring-2 focus-visible:outline-none"
        >
          <ChevronsRightIcon className="size-4" />
        </button>
      </div>
      <div className="text-muted-foreground text-2xs px-3 pt-3 pb-1 font-semibold tracking-wider uppercase">
        My projects
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading ? (
          <div className="flex flex-col gap-2 px-1 pt-1">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        ) : projects.length === 0 ? (
          <p className="text-muted-foreground px-2 py-2 text-xs">
            You are not in any project yet. Join one from All projects.
          </p>
        ) : (
          projects.map((p) => <ProjectBranch key={p.slug} project={p} activeHref={activeHref} />)
        )}
      </div>
      <div className="border-sidebar-border border-t px-3 py-2">
        <Link
          href={allProjectsHref}
          className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
        >
          All projects →
        </Link>
      </div>
    </aside>
  );
}

function ProjectBranch({ project, activeHref }: { project: ProjectNode; activeHref?: string }) {
  const holdsActive = project.entities.some((e) => e.href === activeHref);
  const [open, setOpen] = React.useState(holdsActive);
  const Chevron = open ? ChevronDownIcon : ChevronRightIcon;
  return (
    <div className="mb-0.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="hover:bg-sidebar-accent/60 focus-visible:ring-ring flex h-7 w-full min-w-0 items-center gap-1.5 rounded-md px-1.5 text-left text-sm focus-visible:ring-2 focus-visible:outline-none"
      >
        <Chevron className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
        <span className="truncate font-medium" title={project.name}>
          {project.name}
        </span>
        <span className="text-muted-foreground text-2xs ml-auto font-mono">
          {project.entities.length}
        </span>
      </button>
      {open && (
        <ul className="ml-3 flex flex-col gap-0.5 border-l border-current/10 pl-2">
          {project.entities.map((e) => {
            const Icon = KIND_ICON[e.kind];
            const isActive = e.href === activeHref;
            return (
              <li key={`${e.kind}:${e.key}`}>
                <Link
                  href={e.href}
                  aria-current={isActive ? "page" : undefined}
                  className={cn(
                    "focus-visible:ring-ring flex h-7 min-w-0 items-center gap-2 rounded-md px-1.5 text-sm focus-visible:ring-2 focus-visible:outline-none",
                    isActive
                      ? "text-foreground bg-[var(--brand-primary)]/15 font-medium"
                      : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60"
                  )}
                >
                  <Icon className="size-3.5 shrink-0 opacity-70" aria-hidden />
                  <span className="truncate" title={e.name}>
                    {e.name}
                  </span>
                  {e.status && <StatusDot status={e.status} className="ml-auto" />}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
