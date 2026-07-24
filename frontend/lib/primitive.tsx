import {
  BoltIcon,
  BotIcon,
  BoxIcon,
  DatabaseIcon,
  ListChecksIcon,
  RocketIcon,
  TimerIcon,
  WorkflowIcon,
  type LucideIcon,
} from "lucide-react";

import type { WorkloadKind } from "@/graphql/registry/registry.types";

// The nav primitive an app presents as, derived from its workloads. Mirrors the
// backend nav-tree classification (agent wins → single-kind → bundle).
export type PrimitiveKind =
  | "app"
  | "agent"
  | "workflow"
  | "function"
  | "cronjob"
  | "task"
  | "bundle";

interface PrimitiveMeta {
  label: string;
  Icon: LucideIcon;
  /** One-line description of what the primitive is. */
  blurb: string;
}

export const PRIMITIVE_META: Record<PrimitiveKind, PrimitiveMeta> = {
  app: { label: "App", Icon: RocketIcon, blurb: "A deployed service" },
  agent: { label: "Agent", Icon: BotIcon, blurb: "An autonomous agent" },
  workflow: { label: "Workflow", Icon: WorkflowIcon, blurb: "A multi-step pipeline" },
  function: { label: "Function", Icon: BoltIcon, blurb: "A serverless function" },
  cronjob: { label: "Scheduled job", Icon: TimerIcon, blurb: "Runs on a schedule" },
  task: { label: "Task", Icon: ListChecksIcon, blurb: "A one-off run" },
  bundle: { label: "Bundle", Icon: BoxIcon, blurb: "Several workloads in one app" },
};

// Per-workload-kind chrome (for the bundle grid + workload rows).
export const WORKLOAD_KIND_META: Record<WorkloadKind, { label: string; Icon: LucideIcon }> = {
  deployment: { label: "Deployment", Icon: RocketIcon },
  statefulset: { label: "Stateful set", Icon: DatabaseIcon },
  job: { label: "Job", Icon: ListChecksIcon },
  cronjob: { label: "Cronjob", Icon: TimerIcon },
  task: { label: "Task", Icon: ListChecksIcon },
  agent: { label: "Agent", Icon: BotIcon },
  workflow: { label: "Workflow", Icon: WorkflowIcon },
  function: { label: "Function", Icon: BoltIcon },
};

/**
 * Classify an app into a nav primitive from its workload kinds — the frontend
 * mirror of the backend nav-tree rule (agent wins → single-kind → bundle).
 * `task` absorbs one-off `job` workloads (both are checklist-style one-shots).
 */
export function classifyPrimitive(kinds: WorkloadKind[]): PrimitiveKind {
  const set = new Set(kinds);
  if (set.has("agent")) return "agent";
  if (set.size > 1) return "bundle";
  const only = kinds[0];
  if (only === "workflow") return "workflow";
  if (only === "function") return "function";
  if (only === "cronjob") return "cronjob";
  if (only === "task" || only === "job") return "task";
  return "app";
}
