"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRightIcon,
  BotIcon,
  CheckCircleIcon,
  FileTextIcon,
  GitPullRequestIcon,
  LayoutTemplateIcon,
  LayersIcon,
  ShieldCheckIcon,
  UsersIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageShell } from "@/components/PageShell";

// ─── Types ────────────────────────────────────────────────────────────────────

type PatternKind =
  | "single"
  | "chained"
  | "fan_out"
  | "supervisor_worker"
  | "review_loop"
  | "advisor";

type Template = {
  id: string;
  name: string;
  description: string;
  pattern: PatternKind;
  icon: React.ReactNode;
  stages: string[];
};

// ─── Template data ────────────────────────────────────────────────────────────
// Issue #57 launch set (8 templates). Hardcoded for v1; backend seeding
// via data migration lands in the follow-up.

const TEMPLATES: Template[] = [
  {
    id: "content-moderation",
    name: "Content Moderation",
    description:
      "Parallel policy checks per category (toxicity, PII, spam, brand safety), aggregate verdicts, auto-route, optional human review.",
    pattern: "fan_out",
    icon: <ShieldCheckIcon className="h-5 w-5" />,
    stages: ["Ingest", "Policy checks (fan-out)", "Aggregate", "Auto-route", "Human review"],
  },
  {
    id: "message-review-response",
    name: "Message Review & Response",
    description:
      "Classify intent and sentiment, draft a response, human approval gate, iterate on feedback, deliver.",
    pattern: "review_loop",
    icon: <GitPullRequestIcon className="h-5 w-5" />,
    stages: ["Classify", "Draft response", "Human approval", "Revise", "Deliver"],
  },
  {
    id: "api-action",
    name: "API Action",
    description:
      "Inspect current API state, decide if action is needed, execute the request, verify outcome.",
    pattern: "chained",
    icon: <ArrowRightIcon className="h-5 w-5" />,
    stages: ["Inspect", "Decide", "Execute", "Verify"],
  },
  {
    id: "multi-step-api-workflow",
    name: "Multi-step API Workflow",
    description:
      "Supervisor plans ordered API actions, routes to workers, workers execute in parallel, aggregate results.",
    pattern: "supervisor_worker",
    icon: <LayersIcon className="h-5 w-5" />,
    stages: ["Plan", "Route", "Execute steps (fan-out)", "Aggregate", "Human gate on failure"],
  },
  {
    id: "document-summary-report",
    name: "Document Summary & Report",
    description:
      "Ingest document, parallel chunk summarisation, compile key points, format output, deliver.",
    pattern: "chained",
    icon: <FileTextIcon className="h-5 w-5" />,
    stages: ["Ingest", "Summarise chunks", "Compile", "Format", "Deliver"],
  },
  {
    id: "onboarding-flow",
    name: "Onboarding Flow",
    description:
      "Full multi-system onboarding: provision accounts, roles, licenses and tool access in parallel; send welcome comms; optional kickoff scheduling.",
    pattern: "supervisor_worker",
    icon: <UsersIcon className="h-5 w-5" />,
    stages: [
      "Intake",
      "Provision systems (fan-out)",
      "Verify provisioning",
      "Send welcome comms",
      "Schedule kickoff",
      "Human confirmation gate",
    ],
  },
  {
    id: "resume-screening",
    name: "Resume Screening",
    description:
      "Ingest resumes, extract structured fields, score against job description, rank candidates, human recruiter review, notify candidates.",
    pattern: "fan_out",
    icon: <CheckCircleIcon className="h-5 w-5" />,
    stages: [
      "Ingest",
      "Extract (per resume)",
      "Score against JD",
      "Aggregate & rank",
      "Human review",
      "Notify candidates",
    ],
  },
  {
    id: "support-agent-ooda",
    name: "Support Agent (OODA Loop)",
    description:
      "Observe ticket context, orient on root cause, decide response strategy, act — with human escalation gate and follow-up loop.",
    pattern: "review_loop",
    icon: <BotIcon className="h-5 w-5" />,
    stages: [
      "Observe",
      "Orient",
      "Decide",
      "Act",
      "Human escalation gate",
      "Follow-up loop",
    ],
  },
];

// ─── Pattern badge ────────────────────────────────────────────────────────────

const PATTERN_LABEL: Record<PatternKind, string> = {
  single: "Single",
  chained: "Chained",
  fan_out: "Fan-out",
  supervisor_worker: "Supervisor / Worker",
  review_loop: "Review loop",
  advisor: "Advisor",
};

const PATTERN_VARIANT: Record<
  PatternKind,
  "default" | "secondary" | "outline"
> = {
  single: "outline",
  chained: "outline",
  fan_out: "secondary",
  supervisor_worker: "default",
  review_loop: "secondary",
  advisor: "outline",
};

// ─── Template card ────────────────────────────────────────────────────────────

function TemplateCard({ template }: { template: Template }) {
  const router = useRouter();

  function useTemplate() {
    // Pre-populates the workflow builder with this template's pattern.
    // Full clone mutation (#57 backend) wires in after the GQL schema lands.
    // For now we navigate to the builder with a query param so the builder
    // can pre-select the pattern.
    router.push(
      `/workflows/builder?template=${template.id}&pattern=${template.pattern}`
    );
    toast.success(`"${template.name}" loaded in builder`, {
      description: "Fill in your config, then save.",
    });
  }

  return (
    <Card className="flex flex-col">
      <CardContent className="flex flex-1 flex-col gap-4 p-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="bg-muted text-muted-foreground rounded-md p-2">
            {template.icon}
          </div>
          <Badge variant={PATTERN_VARIANT[template.pattern]} className="text-xs shrink-0">
            {PATTERN_LABEL[template.pattern]}
          </Badge>
        </div>

        {/* Name + description */}
        <div className="flex flex-col gap-1">
          <p className="font-semibold">{template.name}</p>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {template.description}
          </p>
        </div>

        {/* Stage list */}
        <ol className="text-muted-foreground flex flex-col gap-1 text-xs">
          {template.stages.map((stage, i) => (
            <li key={i} className="flex items-baseline gap-1.5">
              <span className="text-muted-foreground/60 shrink-0 tabular-nums">
                {i + 1}.
              </span>
              {stage}
            </li>
          ))}
        </ol>

        {/* CTA */}
        <div className="mt-auto pt-2">
          <Button size="sm" className="w-full" onClick={useTemplate}>
            Use template
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────


export default function WorkflowTemplatesPage() {
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
      {TEMPLATES.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-md border border-dashed py-12 text-center">
          <LayoutTemplateIcon className="text-muted-foreground h-8 w-8" />
          <p className="text-muted-foreground text-sm">No templates available</p>
        </div>
      ) : (
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {TEMPLATES.map((t) => (
            <TemplateCard key={t.id} template={t} />
          ))}
        </div>
      )}
    </PageShell>
  );
}
