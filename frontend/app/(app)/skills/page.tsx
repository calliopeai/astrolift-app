"use client";

import Link from "next/link";
import { gql, useQuery } from "@apollo/client";
import { BookOpenIcon, Loader2Icon, PlusIcon, WrenchIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

// ─── Types ────────────────────────────────────────────────────────────────────

type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  isActive: boolean;
  toolCount: number;
};

type SkillsData = {
  agentSkills: Skill[];
};

// ─── GraphQL ─────────────────────────────────────────────────────────────────

const LIST_SKILLS = gql`
  query ListAgentSkills {
    agentSkills {
      id
      name
      slug
      description
      isActive
      toolCount
    }
  }
`;

// ─── Page ─────────────────────────────────────────────────────────────────────

export const metadata = { title: "Skills · Astrolift" };

export default function SkillsPage() {
  const { data, loading, error } = useQuery<SkillsData>(LIST_SKILLS, {
    fetchPolicy: "cache-and-network",
  });

  const skills = data?.agentSkills ?? [];

  return (
    <PageShell
      title="Skills"
      description="Define and manage agent skills — instructions, scripts, and tool bindings your agents can use."
      actions={
        <Button asChild size="sm">
          <Link href="/skills/new">
            <PlusIcon className="mr-1 h-4 w-4" /> New skill
          </Link>
        </Button>
      }
    >
      {loading && skills.length === 0 && (
        <div className="flex items-center justify-center p-12">
          <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
        </div>
      )}

      {error && (
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
          Error: {error.message}
        </div>
      )}

      {!loading && !error && skills.length === 0 && (
        <EmptyState
          icon={<BookOpenIcon className="size-5" />}
          title="No skills defined"
          description="Skills package instructions, scripts, and tools that agents draw on when running tasks."
          actionHref="/skills/new"
          actionLabel="New skill"
        />
      )}

      {skills.length > 0 && (
        <div className="flex flex-col gap-2">
          {skills.map((skill) => (
            <Link
              key={skill.id}
              href={`/skills/${skill.id}`}
              className="hover:bg-muted/50 flex items-center gap-4 rounded-lg border px-4 py-3 transition-colors"
            >
              <div className="bg-muted rounded-md p-1.5">
                <WrenchIcon className="text-muted-foreground h-4 w-4" />
              </div>

              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{skill.name}</span>
                  <Badge variant={skill.isActive ? "default" : "secondary"} className="text-xs">
                    {skill.isActive ? "Active" : "Inactive"}
                  </Badge>
                </div>
                <span className="text-muted-foreground truncate text-sm">
                  {skill.description ?? (
                    <span className="italic">No description</span>
                  )}
                </span>
              </div>

              <div className="flex shrink-0 flex-col items-end gap-1">
                <Badge variant="outline" className="text-xs">
                  {skill.toolCount} {skill.toolCount === 1 ? "tool" : "tools"}
                </Badge>
                <span className="text-muted-foreground font-mono text-xs">
                  {skill.slug}
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </PageShell>
  );
}
