"use client";

import {
  ActivityIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  GitBranchIcon,
  RocketIcon,
  ZapIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const onboardingSteps = [
  {
    icon: <GitBranchIcon className="size-4" />,
    title: "Connect a Git source",
    body:
      "GitHub, GitLab, Bitbucket, Gitea, or a plain Git URL. We watch the default branch for pushes.",
    state: "todo",
  },
  {
    icon: <ZapIcon className="size-4" />,
    title: "Validate the manifest",
    body:
      "Drop an astrolift.toml at the repo root. The platform parses, normalizes, and stores both the raw text and the parsed model.",
    state: "todo",
  },
  {
    icon: <RocketIcon className="size-4" />,
    title: "Roll the first deployment",
    body:
      "OnboardAppWorkflow handles registry repo, namespace, workload identity, and managed services in one durable Temporal run.",
    state: "todo",
  },
];

export default function AppsPage() {
  return (
    <PageShell
      title="Apps"
      description="Repositories registered for deployment. Each app has workloads, environments, deployments, secrets, and managed services attached."
      actions={
        <Button disabled>
          <RocketIcon className="size-4" />
          Register app
        </Button>
      }
    >
      <Card className="border-dashed">
        <CardContent className="p-6">
          <EmptyState
            icon={<RocketIcon className="size-5" />}
            title="The app registry surface ships in the next milestone"
            description="The data model, RBAC, mutations, and Temporal workflow skeletons are in. The /apps GraphQL fields and the onboarding wizard are next on deck."
          />
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-3">
        {onboardingSteps.map((step, i) => (
          <Card key={step.title}>
            <CardHeader className="flex flex-row items-start gap-3 space-y-0">
              <div className="bg-muted text-muted-foreground rounded-md p-2">
                {step.icon}
              </div>
              <div className="flex-1">
                <CardTitle className="text-sm">
                  Step {i + 1} · {step.title}
                </CardTitle>
              </div>
              <CircleDashedIcon className="text-muted-foreground size-4" />
            </CardHeader>
            <CardContent>
              <CardDescription className="text-sm leading-relaxed">
                {step.body}
              </CardDescription>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>What lands here once the registry is wired</CardTitle>
          <CardDescription>
            Per spec 09 §4 — the most-used surface in the platform.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[
            {
              icon: <RocketIcon className="size-4" />,
              title: "App overview",
              body:
                "Hero with health badge, primary URL, screenshot thumbnail, recent deploys.",
            },
            {
              icon: <GitBranchIcon className="size-4" />,
              title: "Manifest editor",
              body:
                "TOML editor with live validation; preview the normalized JSON before saving.",
            },
            {
              icon: <ZapIcon className="size-4" />,
              title: "Deployments timeline",
              body:
                "Drilldown into a single deploy: workflow timeline, logs, rendered manifests, metrics.",
            },
            {
              icon: <CheckCircle2Icon className="size-4" />,
              title: "Environments",
              body:
                "Pause deploys, require approvals, attach ABAC policies per env.",
            },
            {
              icon: <ActivityIcon className="size-4" />,
              title: "Logs + metrics",
              body:
                "Stream pod/container logs and query Prometheus directly from the dashboard.",
            },
            {
              icon: <RocketIcon className="size-4" />,
              title: "Managed services",
              body:
                "Attach Postgres, Redis, object stores; bindings inject as env vars.",
            },
          ].map((card) => (
            <div
              key={card.title}
              className="flex flex-col gap-2 rounded-md border p-4"
            >
              <div className="flex items-center gap-2 text-sm font-medium">
                <span className="bg-muted text-muted-foreground rounded-md p-1.5">
                  {card.icon}
                </span>
                {card.title}
              </div>
              <p className="text-muted-foreground text-sm leading-relaxed">
                {card.body}
              </p>
            </div>
          ))}
        </CardContent>
      </Card>
    </PageShell>
  );
}
