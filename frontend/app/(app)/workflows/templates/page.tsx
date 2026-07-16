"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRightIcon,
  BotIcon,
  CircleIcon,
  CopyIcon,
  GitPullRequestIcon,
  LayersIcon,
  LayoutTemplateIcon,
  Loader2Icon,
  NetworkIcon,
  WorkflowIcon,
} from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useCloneDefinition,
  useWorkflowDefinitions,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type { WorkflowDefinitionSummary } from "@/graphql/workflows/tiered.types";

// ─── Pattern badge ────────────────────────────────────────────────────────────

const PATTERN_LABEL: Record<string, string> = {
  single: "Single",
  chained: "Chained",
  fan_out: "Fan-out",
  supervisor_worker: "Supervisor / Worker",
  review_loop: "Review loop",
  advisor: "Advisor",
};

const PATTERN_VARIANT: Record<string, "default" | "secondary" | "outline"> = {
  single: "outline",
  chained: "outline",
  fan_out: "secondary",
  supervisor_worker: "default",
  review_loop: "secondary",
  advisor: "outline",
};

const PATTERN_ICON: Record<string, React.ReactNode> = {
  single: <CircleIcon className="h-5 w-5" />,
  chained: <ArrowRightIcon className="h-5 w-5" />,
  fan_out: <NetworkIcon className="h-5 w-5" />,
  supervisor_worker: <LayersIcon className="h-5 w-5" />,
  review_loop: <GitPullRequestIcon className="h-5 w-5" />,
  advisor: <BotIcon className="h-5 w-5" />,
};

// ─── Template card ────────────────────────────────────────────────────────────

function TemplateCard({
  definition,
  canCreate,
  orgId,
}: {
  definition: WorkflowDefinitionSummary;
  canCreate: boolean;
  orgId: string | null;
}) {
  const router = useRouter();
  const [cloneDefinition, { loading: cloning }] = useCloneDefinition();

  const handleClone = async () => {
    const { data } = await cloneDefinition({
      variables: { slug: definition.slug, orgId },
    });
    const result = data?.cloneWorkflowDefinition;
    if (result?.ok && result.slug) {
      toast.success(`Cloned "${definition.name}"`, {
        description: "Your editable copy is ready in the builder.",
      });
      router.push(`/workflows/${result.slug}/builder`);
    } else {
      const errors = result?.errors ?? [];
      if (errors.length > 0) {
        for (const e of errors) {
          toast.error(`${e.field}: ${e.messages.join(", ")}`);
        }
      } else {
        toast.error("Failed to clone template");
      }
    }
  };

  return (
    <Card className="flex flex-col">
      <CardContent className="flex flex-1 flex-col gap-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="bg-muted text-muted-foreground rounded-md p-2">
            {PATTERN_ICON[definition.patternKind] ?? (
              <WorkflowIcon className="h-5 w-5" />
            )}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <Badge
              variant={PATTERN_VARIANT[definition.patternKind] ?? "outline"}
              className="text-xs"
            >
              {PATTERN_LABEL[definition.patternKind] ?? definition.patternKind}
            </Badge>
            <Badge variant="outline" className="text-xs">
              Platform template
            </Badge>
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <p className="font-semibold">{definition.name}</p>
          {definition.description && (
            <p className="text-muted-foreground text-sm leading-relaxed">
              {definition.description}
            </p>
          )}
        </div>

        <p className="text-muted-foreground text-xs">
          {definition.stageCount} {definition.stageCount === 1 ? "stage" : "stages"}
        </p>

        {canCreate && (
          <div className="mt-auto flex flex-col gap-2 pt-2">
            <Button size="sm" asChild>
              <Link href={`/workflows/new?definition=${definition.slug}`}>
                Create workflow from
              </Link>
            </Button>
            <Button size="sm" variant="outline" onClick={handleClone} disabled={cloning}>
              {cloning ? (
                <Loader2Icon className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <CopyIcon className="mr-2 h-4 w-4" />
              )}
              Clone to edit
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function WorkflowTemplatesPage() {
  const { org } = useActiveOrg();
  const { definitions, loading, error } = useWorkflowDefinitions(org?.id ?? null);
  const { canCreate } = useWorkflowsEntitlement();

  const templates = definitions.filter((d) => d.isGlobal);

  return (
    <PageShell
      title="Workflow templates"
      description="Pre-built workflow templates operators can clone and configure. Global templates are read-only — clone to create an editable copy."
      actions={
        <Button asChild variant="outline" size="sm">
          <Link href="/workflows">← All workflows</Link>
        </Button>
      }
    >
      <Section
        title="Platform templates"
        description="Global definitions curated by the platform."
      >
        {loading && templates.length === 0 ? (
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="flex flex-col">
                <CardContent className="flex flex-1 flex-col gap-4 p-5">
                  <div className="flex items-start justify-between gap-3">
                    <Skeleton className="h-9 w-9" />
                    <Skeleton className="h-5 w-20" />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Skeleton className="h-5 w-2/3" />
                    <Skeleton className="h-4 w-full" />
                    <Skeleton className="h-4 w-4/5" />
                  </div>
                  <Skeleton className="mt-auto h-8 w-full" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : error ? (
          <EmptyState
            icon={<LayoutTemplateIcon className="size-5" />}
            title="Couldn't load templates"
            description={error.message}
          />
        ) : templates.length === 0 ? (
          <EmptyState
            icon={<LayoutTemplateIcon className="size-5" />}
            title="No templates available"
            description="No global workflow templates have been published yet."
            actionHref="/workflows"
            actionLabel="Back to workflows"
          />
        ) : (
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {templates.map((d) => (
              <TemplateCard
                key={d.guid}
                definition={d}
                canCreate={canCreate}
                orgId={org?.id ?? null}
              />
            ))}
          </div>
        )}
      </Section>
    </PageShell>
  );
}
