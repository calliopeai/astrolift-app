import Link from "next/link";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  BotIcon,
  RocketIcon,
  ServerIcon,
} from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const metadata = {
  title: "Operator runbooks · Documentation · Astrolift",
};

const runbooks = [
  {
    href: "/documentation/runbooks/deploy",
    icon: RocketIcon,
    title: "Deploy workflow",
    description:
      "Standard deploy flow, monitoring, rollback, and zero-downtime considerations.",
  },
  {
    href: "/documentation/runbooks/cluster-management",
    icon: ServerIcon,
    title: "Cluster management",
    description:
      "Bring a cluster into management, rotate credentials, and controlled decommission.",
  },
  {
    href: "/documentation/runbooks/agent-dispatch",
    icon: BotIcon,
    title: "Agent dispatch",
    description:
      "Dispatch, monitor, cancel, and recover stuck agent tasks.",
  },
  {
    href: "/documentation/runbooks/incident-response",
    icon: AlertTriangleIcon,
    title: "Incident response",
    description:
      "Triage guide for common failure modes: app down, deploy stuck, control plane unreachable, cluster degraded.",
  },
];

export default function RunbooksIndexPage() {
  return (
    <div className="flex flex-1 flex-col gap-8 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Operator runbooks</h1>
        <p className="text-muted-foreground mt-2 max-w-2xl text-sm">
          Step-by-step procedures for core platform operations and failure
          recovery. These runbooks cover the workflows operators encounter
          day-to-day and under incident conditions.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {runbooks.map((rb) => {
          const Icon = rb.icon;
          return (
            <Link key={rb.href} href={rb.href} className="group">
              <Card className="flex h-full flex-col transition-colors group-hover:border-foreground/20">
                <CardHeader className="pb-2">
                  <div className="bg-muted text-foreground/80 inline-flex size-9 items-center justify-center rounded-md">
                    <Icon className="size-4" />
                  </div>
                  <CardTitle className="mt-3 text-base">{rb.title}</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-1 flex-col justify-between gap-3">
                  <CardDescription>{rb.description}</CardDescription>
                  <span className="text-muted-foreground group-hover:text-foreground flex items-center gap-1 text-xs transition-colors">
                    Read runbook
                    <ArrowRightIcon className="h-3 w-3" />
                  </span>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
