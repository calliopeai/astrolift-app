"use client";

import Link from "next/link";
import { useQuery } from "@apollo/client/react";
import {
  BookOpenIcon,
  GitBranchIcon,
  GlobeIcon,
  Loader2Icon,
  PlusIcon,
  WrenchIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { LIST_SKILLS } from "@/graphql/agents/agents.queries";
import { getActiveOrgGuid } from "@/lib/identity/active-org";

// ─── Types ────────────────────────────────────────────────────────────────────

type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string;
  skillVersion: number;
  isGlobal: boolean;
  isActive: boolean;
  agentType?: string;
};

type SkillsData = {
  skills: Skill[];
};

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AgentSkillsPage() {
  const orgId = getActiveOrgGuid() ?? "";

  const { data, loading, error } = useQuery<SkillsData>(LIST_SKILLS, {
    variables: { orgId },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });

  const skills = data?.skills ?? [];
  const ownSkills = skills.filter((s) => !s.isGlobal);
  const globalSkills = skills.filter((s) => s.isGlobal);

  return (
    <PageShell
      title="Skill registry"
      description="Versioned instructions and tool bindings your agents draw on at dispatch time."
      actions={
        <div className="flex items-center gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/agents/skills/import">
              <GitBranchIcon className="mr-1 h-4 w-4" /> Import from repo
            </Link>
          </Button>
          <Button asChild size="sm">
            <Link href="/agents/skills/new">
              <PlusIcon className="mr-1 h-4 w-4" /> New skill
            </Link>
          </Button>
        </div>
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
          description="Skills package instructions and tools that agents draw on when running tasks."
          actionHref="/agents/skills/new"
          actionLabel="New skill"
        />
      )}

      {ownSkills.length > 0 && (
        <section className="flex flex-col gap-2">
          {ownSkills.map((skill) => (
            <SkillRow key={skill.id} skill={skill} />
          ))}
        </section>
      )}

      {globalSkills.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold">
            <GlobeIcon className="text-muted-foreground h-3.5 w-3.5" />
            Global skills
          </h2>
          <div className="flex flex-col gap-2">
            {globalSkills.map((skill) => (
              <SkillRow key={skill.id} skill={skill} />
            ))}
          </div>
        </section>
      )}
    </PageShell>
  );
}

function SkillRow({ skill }: { skill: Skill }) {
  return (
    <Link
      href={`/agents/skills/${skill.id}`}
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
          {skill.isGlobal && (
            <Badge variant="outline" className="text-xs">
              Global
            </Badge>
          )}
          {skill.agentType && (
            <Badge variant="outline" className="text-muted-foreground text-xs">
              {skill.agentType}
            </Badge>
          )}
        </div>
        <span className="text-muted-foreground truncate text-sm">
          {skill.description || <span className="italic">No description</span>}
        </span>
      </div>

      <div className="flex shrink-0 flex-col items-end gap-0.5">
        <span className="text-muted-foreground font-mono text-xs">{skill.slug}</span>
        <span className="text-muted-foreground text-xs">v{skill.skillVersion}</span>
      </div>
    </Link>
  );
}
