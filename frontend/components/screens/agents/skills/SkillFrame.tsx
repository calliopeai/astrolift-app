"use client";

import { AlertTriangleIcon, ServerCrashIcon } from "lucide-react";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { agentsCrumbs } from "./catalog";
import type { Skill } from "./use-skill";

export type SkillTab = "builder" | "tools";

const TABS: { key: SkillTab; label: string; segment: string }[] = [
  { key: "builder", label: "Builder", segment: "" },
  { key: "tools", label: "Tools", segment: "/tools" },
];

export function skillTabHref(id: string, tab: SkillTab): string {
  return `/agents/skills/${id}${TABS.find((t) => t.key === tab)?.segment ?? ""}`;
}

export interface SkillFrameProps {
  id: string;
  active: SkillTab;
  skill: Skill | null;
  loading: boolean;
  error: string | null;
  onRetry?: () => void;
  primaryAction?: React.ReactNode;
  menu?: React.ReactNode;
  children: React.ReactNode;
}

/**
 * A skill's detail frame (spec 44 §5.2): `Agents ▾ › Skills › name`, the
 * title row (name, status, slug and version, the tab's primary action, `⋯`),
 * and one row of tabs, Builder and Tools, each its own route. Loading, error
 * and not found are the frame's, so a tab only draws its body. Pure.
 */
export function SkillFrame({
  id,
  active,
  skill,
  loading,
  error,
  onRetry,
  primaryAction,
  menu,
  children,
}: SkillFrameProps) {
  const tabs = TABS.map((t) => ({
    key: t.key,
    label: t.label,
    href: skillTabHref(id, t.key),
    active: t.key === active,
  }));

  if (!skill) {
    const pending = loading && !error;
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ShellHeader
          crumbs={agentsCrumbs("skills", { label: pending ? "Loading" : "Not found" })}
          title={pending ? <Skeleton className="h-6 w-48" /> : "Skill not found"}
          tabs={pending ? tabs : undefined}
          tabsAriaLabel="Skill sections"
        />
        {pending ? (
          <div className="grid min-w-0 grid-cols-12 gap-4" aria-busy>
            <Skeleton className="col-span-12 h-40 w-full xl:col-span-8" />
            <Skeleton className="col-span-12 h-40 w-full xl:col-span-4" />
            <Skeleton className="col-span-12 h-64 w-full" />
          </div>
        ) : error ? (
          <div
            role="alert"
            className="flex flex-col items-center gap-3 rounded-md border py-10 text-center"
          >
            <ServerCrashIcon className="text-danger size-5" aria-hidden />
            <div className="min-w-0 px-6">
              <p className="font-medium">Couldn&apos;t load this skill</p>
              <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
                {error}
              </p>
            </div>
            {onRetry && (
              <Button size="sm" variant="outline" onClick={onRetry}>
                Retry
              </Button>
            )}
          </div>
        ) : (
          <EmptyState
            icon={<AlertTriangleIcon className="size-5" />}
            title="Skill not found"
            description="This skill may have been deleted, or you may not have access to it."
            actionHref="/agents/skills"
            actionLabel="Back to skills"
          />
        )}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={agentsCrumbs("skills", { label: skill.name })}
        title={<span title={skill.name}>{skill.name}</span>}
        status={
          <span className="inline-flex shrink-0 items-center gap-2 text-sm">
            <span className="inline-flex items-center gap-1.5">
              <StatusDot status={skill.isActive ? "ok" : "muted"} />
              {skill.isActive ? "Active" : "Inactive"}
            </span>
            {skill.isGlobal && <Badge variant="outline">Global</Badge>}
          </span>
        }
        context={
          <span className="font-mono text-xs" title={skill.slug}>
            {skill.slug} · v{skill.skillVersion}
          </span>
        }
        primaryAction={primaryAction}
        menu={menu}
        tabs={tabs}
        tabsAriaLabel="Skill sections"
      />
      {children}
    </div>
  );
}
